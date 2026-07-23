// SPDX-License-Identifier: GPL-2.0
#include <linux/atomic.h>
#include <linux/dmaengine.h>
#include <linux/dma-mapping.h>
#include <linux/fs.h>
#include <linux/io.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/poll.h>
#include <linux/slab.h>
#include <linux/spinlock.h>
#include <linux/uaccess.h>
#include <linux/wait.h>

#include "cora_pwm_ioctl.h"

#define DRIVER_NAME "cora_pwm"

#define PWM_CONTROL 0x00
#define PWM_PERIOD_TICKS 0x04
#define PWM_COMMAND 0x08
#define PWM_ACTIVE_PERIOD 0x10
#define PWM_ACTIVE_DUTY 0x14
#define PWM_FIFO_LEVEL 0x18
#define PWM_UNDERRUN_COUNT 0x1c
#define PWM_SAMPLE_COUNT_LO 0x20
#define PWM_SAMPLE_COUNT_HI 0x24
#define PWM_CORE_ID_REG 0x28
#define PWM_CORE_VERSION_REG 0x2c

#define PWM_CONTROL_ENABLE BIT(0)
#define PWM_CONTROL_INVERT BIT(1)
#define PWM_CONTROL_DITHER BIT(2)

#define CORA_PWM_CLOCK_HZ 200000000U
#define CORA_PWM_DEFAULT_PERIOD_TICKS 2000U
#define CORA_PWM_PERIOD_BYTES 4096U
#define CORA_PWM_RING_PERIODS 8U
#define CORA_PWM_RING_BYTES (CORA_PWM_PERIOD_BYTES * CORA_PWM_RING_PERIODS)

enum cora_slot_state {
	CORA_SLOT_FREE,
	CORA_SLOT_FILLING,
	CORA_SLOT_READY,
};

struct cora_pwm_dev {
	struct device *dev;
	void __iomem *regs;
	struct dma_chan *tx_chan;
	struct device *dma_dev;
	void *ring_cpu;
	dma_addr_t ring_dma;
	struct miscdevice miscdev;
	struct mutex io_lock; /* Serializes file operations and DMA setup. */
	spinlock_t state_lock; /* Protects ring ownership and counters. */
	wait_queue_head_t write_wait;
	atomic_t opened;
	enum cora_slot_state slots[CORA_PWM_RING_PERIODS];
	u32 producer_slot;
	u32 completed_slot;
	u32 queued_periods;
	u32 config_flags;
	u32 period_ticks;
	bool running;
	bool faulted;
	u64 dma_periods;
	u64 driver_underruns;
};

static u32 cora_pwm_control_value(struct cora_pwm_dev *pwm, bool enable)
{
	u32 value = 0;

	if (enable)
		value |= PWM_CONTROL_ENABLE;
	if (pwm->config_flags & CORA_PWM_CFG_INVERT)
		value |= PWM_CONTROL_INVERT;
	if (pwm->config_flags & CORA_PWM_CFG_DITHER)
		value |= PWM_CONTROL_DITHER;

	return value;
}

static void cora_pwm_disable_output(struct cora_pwm_dev *pwm)
{
	writel(cora_pwm_control_value(pwm, false), pwm->regs + PWM_CONTROL);
}

static void cora_pwm_reset_ring(struct cora_pwm_dev *pwm)
{
	unsigned long flags;
	u32 slot;

	memset(pwm->ring_cpu, 0, CORA_PWM_RING_BYTES);

	spin_lock_irqsave(&pwm->state_lock, flags);
	for (slot = 0; slot < CORA_PWM_RING_PERIODS; slot++)
		pwm->slots[slot] = CORA_SLOT_FREE;
	pwm->producer_slot = 0;
	/* The Xilinx cyclic callback first fires as the final ring BD completes. */
	pwm->completed_slot = CORA_PWM_RING_PERIODS - 1;
	pwm->queued_periods = 0;
	pwm->running = false;
	pwm->faulted = false;
	spin_unlock_irqrestore(&pwm->state_lock, flags);

	wake_up_interruptible(&pwm->write_wait);
}

static bool cora_pwm_can_accept(struct cora_pwm_dev *pwm)
{
	unsigned long flags;
	bool ready;

	spin_lock_irqsave(&pwm->state_lock, flags);
	ready = pwm->faulted ||
		pwm->slots[pwm->producer_slot] == CORA_SLOT_FREE;
	spin_unlock_irqrestore(&pwm->state_lock, flags);

	return ready;
}

static void cora_pwm_dma_callback(void *argument)
{
	struct cora_pwm_dev *pwm = argument;
	unsigned long flags;
	u32 completed;
	bool underrun = false;

	spin_lock_irqsave(&pwm->state_lock, flags);
	if (!pwm->running) {
		spin_unlock_irqrestore(&pwm->state_lock, flags);
		return;
	}

	completed = pwm->completed_slot;
	if (pwm->slots[completed] != CORA_SLOT_READY) {
		underrun = true;
	} else {
		pwm->slots[completed] = CORA_SLOT_FREE;
		pwm->queued_periods--;
		pwm->dma_periods++;
		pwm->completed_slot = (completed + 1) % CORA_PWM_RING_PERIODS;
		if (pwm->slots[pwm->completed_slot] != CORA_SLOT_READY)
			underrun = true;
	}

	if (underrun) {
		pwm->running = false;
		pwm->faulted = true;
		pwm->driver_underruns++;
	}
	spin_unlock_irqrestore(&pwm->state_lock, flags);

	if (!underrun) {
		wake_up_interruptible(&pwm->write_wait);
		return;
	}

	/* terminate_async() is callback-safe; the Xilinx driver resets the channel. */
	cora_pwm_disable_output(pwm);
	dmaengine_terminate_async(pwm->tx_chan);
	wake_up_interruptible(&pwm->write_wait);
}

static int cora_pwm_start_locked(struct cora_pwm_dev *pwm)
{
	struct dma_async_tx_descriptor *descriptor;
	dma_cookie_t cookie;
	unsigned long flags;

	descriptor = dmaengine_prep_dma_cyclic(pwm->tx_chan, pwm->ring_dma,
					       CORA_PWM_RING_BYTES,
					       CORA_PWM_PERIOD_BYTES,
					       DMA_MEM_TO_DEV,
					       DMA_PREP_INTERRUPT | DMA_CTRL_ACK);
	if (!descriptor)
		return -EIO;

	descriptor->callback = cora_pwm_dma_callback;
	descriptor->callback_param = pwm;
	cookie = dmaengine_submit(descriptor);
	if (dma_submit_error(cookie))
		return dma_submit_error(cookie);

	spin_lock_irqsave(&pwm->state_lock, flags);
	pwm->completed_slot = CORA_PWM_RING_PERIODS - 1;
	pwm->running = true;
	spin_unlock_irqrestore(&pwm->state_lock, flags);

	writel(pwm->period_ticks, pwm->regs + PWM_PERIOD_TICKS);
	dma_async_issue_pending(pwm->tx_chan);
	writel(cora_pwm_control_value(pwm, true), pwm->regs + PWM_CONTROL);

	return 0;
}

static void cora_pwm_stop_locked(struct cora_pwm_dev *pwm)
{
	cora_pwm_disable_output(pwm);
	dmaengine_terminate_sync(pwm->tx_chan);
	cora_pwm_reset_ring(pwm);
}

static int cora_pwm_open(struct inode *inode, struct file *file)
{
	struct miscdevice *misc = file->private_data;
	struct cora_pwm_dev *pwm = container_of(misc, struct cora_pwm_dev, miscdev);

	if (atomic_cmpxchg(&pwm->opened, 0, 1))
		return -EBUSY;

	file->private_data = pwm;
	mutex_lock(&pwm->io_lock);
	cora_pwm_stop_locked(pwm);
	writel(pwm->period_ticks, pwm->regs + PWM_PERIOD_TICKS);
	writel(1, pwm->regs + PWM_COMMAND);
	mutex_unlock(&pwm->io_lock);

	return nonseekable_open(inode, file);
}

static int cora_pwm_release(struct inode *inode, struct file *file)
{
	struct cora_pwm_dev *pwm = file->private_data;

	mutex_lock(&pwm->io_lock);
	cora_pwm_stop_locked(pwm);
	mutex_unlock(&pwm->io_lock);
	atomic_set(&pwm->opened, 0);
	wake_up_interruptible(&pwm->write_wait);

	return 0;
}

static ssize_t cora_pwm_write(struct file *file, const char __user *buffer,
			      size_t count, loff_t *position)
{
	struct cora_pwm_dev *pwm = file->private_data;
	size_t written = 0;
	int ret = 0;

	if (!count)
		return 0;
	if (count % CORA_PWM_PERIOD_BYTES)
		return -EINVAL;

	mutex_lock(&pwm->io_lock);
	while (written < count) {
		unsigned long flags;
		u32 slot;
		bool start;

		spin_lock_irqsave(&pwm->state_lock, flags);
		if (pwm->faulted) {
			spin_unlock_irqrestore(&pwm->state_lock, flags);
			dmaengine_synchronize(pwm->tx_chan);
			cora_pwm_reset_ring(pwm);
		} else {
			spin_unlock_irqrestore(&pwm->state_lock, flags);
		}

		while (!cora_pwm_can_accept(pwm)) {
			mutex_unlock(&pwm->io_lock);
			if (file->f_flags & O_NONBLOCK)
				return written ? written : -EAGAIN;
			ret = wait_event_interruptible(pwm->write_wait,
						       cora_pwm_can_accept(pwm));
			if (ret)
				return written ? written : ret;
			mutex_lock(&pwm->io_lock);
		}

		spin_lock_irqsave(&pwm->state_lock, flags);
		slot = pwm->producer_slot;
		if (pwm->slots[slot] != CORA_SLOT_FREE) {
			spin_unlock_irqrestore(&pwm->state_lock, flags);
			continue;
		}
		pwm->slots[slot] = CORA_SLOT_FILLING;
		spin_unlock_irqrestore(&pwm->state_lock, flags);

		if (copy_from_user(pwm->ring_cpu + slot * CORA_PWM_PERIOD_BYTES,
				   buffer + written, CORA_PWM_PERIOD_BYTES)) {
			spin_lock_irqsave(&pwm->state_lock, flags);
			pwm->slots[slot] = CORA_SLOT_FREE;
			spin_unlock_irqrestore(&pwm->state_lock, flags);
			ret = -EFAULT;
			break;
		}

		spin_lock_irqsave(&pwm->state_lock, flags);
		if (pwm->faulted) {
			pwm->slots[slot] = CORA_SLOT_FREE;
			spin_unlock_irqrestore(&pwm->state_lock, flags);
			ret = -EPIPE;
			break;
		}
		pwm->slots[slot] = CORA_SLOT_READY;
		pwm->producer_slot = (slot + 1) % CORA_PWM_RING_PERIODS;
		pwm->queued_periods++;
		start = !pwm->running &&
			pwm->queued_periods == CORA_PWM_RING_PERIODS;
		spin_unlock_irqrestore(&pwm->state_lock, flags);

		written += CORA_PWM_PERIOD_BYTES;
		if (start) {
			ret = cora_pwm_start_locked(pwm);
			if (ret) {
				cora_pwm_reset_ring(pwm);
				break;
			}
		}
	}
	mutex_unlock(&pwm->io_lock);

	return written ? written : ret;
}

static u64 cora_pwm_read_sample_count(struct cora_pwm_dev *pwm)
{
	u32 high_before, high_after, low;

	do {
		high_before = readl(pwm->regs + PWM_SAMPLE_COUNT_HI);
		low = readl(pwm->regs + PWM_SAMPLE_COUNT_LO);
		high_after = readl(pwm->regs + PWM_SAMPLE_COUNT_HI);
	} while (high_before != high_after);

	return ((u64)high_after << 32) | low;
}

struct cora_pwm_sysfs_status {
	u32 running;
	u32 opened;
	u32 faulted;
	u32 period_ticks;
	u32 queued_periods;
	u32 fifo_level;
	u32 active_period;
	u32 active_duty;
	u32 pwm_underruns;
	u64 dma_periods;
	u64 driver_underruns;
	u64 accepted_samples;
};

static void cora_pwm_get_sysfs_status(struct cora_pwm_dev *pwm,
				      struct cora_pwm_sysfs_status *status)
{
	unsigned long flags;

	spin_lock_irqsave(&pwm->state_lock, flags);
	status->running = pwm->running;
	status->opened = atomic_read(&pwm->opened);
	status->faulted = pwm->faulted;
	status->period_ticks = pwm->period_ticks;
	status->queued_periods = pwm->queued_periods;
	status->dma_periods = pwm->dma_periods;
	status->driver_underruns = pwm->driver_underruns;
	spin_unlock_irqrestore(&pwm->state_lock, flags);

	status->fifo_level = readl(pwm->regs + PWM_FIFO_LEVEL);
	status->active_period = readl(pwm->regs + PWM_ACTIVE_PERIOD);
	status->active_duty = readl(pwm->regs + PWM_ACTIVE_DUTY);
	status->pwm_underruns = readl(pwm->regs + PWM_UNDERRUN_COUNT);
	status->accepted_samples = cora_pwm_read_sample_count(pwm);
}

static ssize_t cora_pwm_u32_show(struct device *dev,
				 struct device_attribute *attribute, char *buffer)
{
	struct cora_pwm_sysfs_status status = { 0 };
	u32 value;

	cora_pwm_get_sysfs_status(dev_get_drvdata(dev), &status);
	if (!strcmp(attribute->attr.name, "running"))
		value = status.running;
	else if (!strcmp(attribute->attr.name, "opened"))
		value = status.opened;
	else if (!strcmp(attribute->attr.name, "faulted"))
		value = status.faulted;
	else if (!strcmp(attribute->attr.name, "period_ticks"))
		value = status.period_ticks;
	else if (!strcmp(attribute->attr.name, "queued_periods"))
		value = status.queued_periods;
	else if (!strcmp(attribute->attr.name, "fifo_level"))
		value = status.fifo_level;
	else if (!strcmp(attribute->attr.name, "active_period"))
		value = status.active_period;
	else if (!strcmp(attribute->attr.name, "active_duty"))
		value = status.active_duty;
	else if (!strcmp(attribute->attr.name, "pwm_underruns"))
		value = status.pwm_underruns;
	else
		return -EINVAL;

	return sysfs_emit(buffer, "%u\n", value);
}

static ssize_t cora_pwm_u64_show(struct device *dev,
				 struct device_attribute *attribute, char *buffer)
{
	struct cora_pwm_sysfs_status status = { 0 };
	u64 value;

	cora_pwm_get_sysfs_status(dev_get_drvdata(dev), &status);
	if (!strcmp(attribute->attr.name, "dma_periods"))
		value = status.dma_periods;
	else if (!strcmp(attribute->attr.name, "driver_underruns"))
		value = status.driver_underruns;
	else if (!strcmp(attribute->attr.name, "accepted_samples"))
		value = status.accepted_samples;
	else
		return -EINVAL;

	return sysfs_emit(buffer, "%llu\n", (unsigned long long)value);
}

static ssize_t running_show(struct device *dev,
			    struct device_attribute *attribute, char *buffer)
{
	return cora_pwm_u32_show(dev, attribute, buffer);
}
static DEVICE_ATTR_RO(running);

static ssize_t opened_show(struct device *dev,
			   struct device_attribute *attribute, char *buffer)
{
	return cora_pwm_u32_show(dev, attribute, buffer);
}
static DEVICE_ATTR_RO(opened);

static ssize_t faulted_show(struct device *dev,
			    struct device_attribute *attribute, char *buffer)
{
	return cora_pwm_u32_show(dev, attribute, buffer);
}
static DEVICE_ATTR_RO(faulted);

static ssize_t period_ticks_show(struct device *dev,
				 struct device_attribute *attribute, char *buffer)
{
	return cora_pwm_u32_show(dev, attribute, buffer);
}
static DEVICE_ATTR_RO(period_ticks);

static ssize_t queued_periods_show(struct device *dev,
				   struct device_attribute *attribute,
				   char *buffer)
{
	return cora_pwm_u32_show(dev, attribute, buffer);
}
static DEVICE_ATTR_RO(queued_periods);

static ssize_t fifo_level_show(struct device *dev,
			       struct device_attribute *attribute, char *buffer)
{
	return cora_pwm_u32_show(dev, attribute, buffer);
}
static DEVICE_ATTR_RO(fifo_level);

static ssize_t active_period_show(struct device *dev,
				  struct device_attribute *attribute,
				  char *buffer)
{
	return cora_pwm_u32_show(dev, attribute, buffer);
}
static DEVICE_ATTR_RO(active_period);

static ssize_t active_duty_show(struct device *dev,
				struct device_attribute *attribute, char *buffer)
{
	return cora_pwm_u32_show(dev, attribute, buffer);
}
static DEVICE_ATTR_RO(active_duty);

static ssize_t pwm_underruns_show(struct device *dev,
				  struct device_attribute *attribute,
				  char *buffer)
{
	return cora_pwm_u32_show(dev, attribute, buffer);
}
static DEVICE_ATTR_RO(pwm_underruns);

static ssize_t dma_periods_show(struct device *dev,
				struct device_attribute *attribute, char *buffer)
{
	return cora_pwm_u64_show(dev, attribute, buffer);
}
static DEVICE_ATTR_RO(dma_periods);

static ssize_t driver_underruns_show(struct device *dev,
				     struct device_attribute *attribute,
				     char *buffer)
{
	return cora_pwm_u64_show(dev, attribute, buffer);
}
static DEVICE_ATTR_RO(driver_underruns);

static ssize_t accepted_samples_show(struct device *dev,
				     struct device_attribute *attribute,
				     char *buffer)
{
	return cora_pwm_u64_show(dev, attribute, buffer);
}
static DEVICE_ATTR_RO(accepted_samples);

static ssize_t stop_store(struct device *dev, struct device_attribute *attribute,
			  const char *buffer, size_t count)
{
	struct cora_pwm_dev *pwm = dev_get_drvdata(dev);
	bool requested;
	int ret;

	ret = kstrtobool(buffer, &requested);
	if (ret)
		return ret;
	if (!requested)
		return -EINVAL;

	mutex_lock(&pwm->io_lock);
	cora_pwm_stop_locked(pwm);
	mutex_unlock(&pwm->io_lock);
	return count;
}
static DEVICE_ATTR_WO(stop);

static ssize_t clear_stats_store(struct device *dev,
				 struct device_attribute *attribute,
				 const char *buffer, size_t count)
{
	struct cora_pwm_dev *pwm = dev_get_drvdata(dev);
	unsigned long flags;
	bool requested;
	int ret;

	ret = kstrtobool(buffer, &requested);
	if (ret)
		return ret;
	if (!requested)
		return -EINVAL;

	spin_lock_irqsave(&pwm->state_lock, flags);
	pwm->dma_periods = 0;
	pwm->driver_underruns = 0;
	spin_unlock_irqrestore(&pwm->state_lock, flags);
	writel(1, pwm->regs + PWM_COMMAND);
	return count;
}
static DEVICE_ATTR_WO(clear_stats);

static struct attribute *cora_pwm_attributes[] = {
	&dev_attr_running.attr,
	&dev_attr_opened.attr,
	&dev_attr_faulted.attr,
	&dev_attr_period_ticks.attr,
	&dev_attr_queued_periods.attr,
	&dev_attr_fifo_level.attr,
	&dev_attr_active_period.attr,
	&dev_attr_active_duty.attr,
	&dev_attr_pwm_underruns.attr,
	&dev_attr_dma_periods.attr,
	&dev_attr_driver_underruns.attr,
	&dev_attr_accepted_samples.attr,
	&dev_attr_stop.attr,
	&dev_attr_clear_stats.attr,
	NULL,
};

static const struct attribute_group cora_pwm_attribute_group = {
	.attrs = cora_pwm_attributes,
};

static long cora_pwm_ioctl(struct file *file, unsigned int command,
			   unsigned long argument)
{
	struct cora_pwm_dev *pwm = file->private_data;
	void __user *user_argument = (void __user *)argument;
	unsigned long flags;

	switch (command) {
	case CORA_PWM_IOC_GET_INFO: {
		struct cora_pwm_info info = {
			.abi_version = CORA_PWM_ABI_VERSION,
			.core_id = readl(pwm->regs + PWM_CORE_ID_REG),
			.core_version = readl(pwm->regs + PWM_CORE_VERSION_REG),
			.pwm_clock_hz = CORA_PWM_CLOCK_HZ,
			.period_bytes = CORA_PWM_PERIOD_BYTES,
			.ring_periods = CORA_PWM_RING_PERIODS,
			.sample_bytes = sizeof(s16),
		};

		return copy_to_user(user_argument, &info, sizeof(info)) ? -EFAULT : 0;
	}
	case CORA_PWM_IOC_SET_CONFIG: {
		struct cora_pwm_config config;
		bool running;

		if (copy_from_user(&config, user_argument, sizeof(config)))
			return -EFAULT;
		if (config.period_ticks < 2 || config.period_ticks > 65535)
			return -EINVAL;
		if (config.flags & ~(CORA_PWM_CFG_INVERT | CORA_PWM_CFG_DITHER))
			return -EINVAL;

		mutex_lock(&pwm->io_lock);
		spin_lock_irqsave(&pwm->state_lock, flags);
		running = pwm->running;
		spin_unlock_irqrestore(&pwm->state_lock, flags);
		if (!running) {
			pwm->period_ticks = config.period_ticks;
			pwm->config_flags = config.flags;
			writel(pwm->period_ticks, pwm->regs + PWM_PERIOD_TICKS);
			cora_pwm_disable_output(pwm);
		}
		mutex_unlock(&pwm->io_lock);

		return running ? -EBUSY : 0;
	}
	case CORA_PWM_IOC_GET_STATUS: {
		struct cora_pwm_status status = { 0 };
		u32 queued;

		spin_lock_irqsave(&pwm->state_lock, flags);
		if (pwm->running)
			status.flags |= CORA_PWM_STATUS_RUNNING;
		if (atomic_read(&pwm->opened))
			status.flags |= CORA_PWM_STATUS_OPEN;
		queued = pwm->queued_periods;
		status.queued_periods = queued;
		status.free_periods = CORA_PWM_RING_PERIODS - queued;
		status.dma_periods = pwm->dma_periods;
		status.driver_underruns = pwm->driver_underruns;
		spin_unlock_irqrestore(&pwm->state_lock, flags);

		status.pwm_fifo_level = readl(pwm->regs + PWM_FIFO_LEVEL);
		status.active_period = readl(pwm->regs + PWM_ACTIVE_PERIOD);
		status.active_duty = readl(pwm->regs + PWM_ACTIVE_DUTY);
		status.pwm_underruns = readl(pwm->regs + PWM_UNDERRUN_COUNT);
		status.accepted_samples = cora_pwm_read_sample_count(pwm);

		return copy_to_user(user_argument, &status, sizeof(status)) ? -EFAULT : 0;
	}
	case CORA_PWM_IOC_STOP:
		mutex_lock(&pwm->io_lock);
		cora_pwm_stop_locked(pwm);
		mutex_unlock(&pwm->io_lock);
		return 0;
	case CORA_PWM_IOC_CLEAR_STATS:
		spin_lock_irqsave(&pwm->state_lock, flags);
		pwm->dma_periods = 0;
		pwm->driver_underruns = 0;
		spin_unlock_irqrestore(&pwm->state_lock, flags);
		writel(1, pwm->regs + PWM_COMMAND);
		return 0;
	default:
		return -ENOTTY;
	}
}

static __poll_t cora_pwm_poll(struct file *file, poll_table *wait)
{
	struct cora_pwm_dev *pwm = file->private_data;
	__poll_t events = 0;

	poll_wait(file, &pwm->write_wait, wait);
	if (cora_pwm_can_accept(pwm))
		events |= EPOLLOUT | EPOLLWRNORM;

	return events;
}

static const struct file_operations cora_pwm_fops = {
	.owner = THIS_MODULE,
	.open = cora_pwm_open,
	.release = cora_pwm_release,
	.write = cora_pwm_write,
	.unlocked_ioctl = cora_pwm_ioctl,
#ifdef CONFIG_COMPAT
	.compat_ioctl = cora_pwm_ioctl,
#endif
	.poll = cora_pwm_poll,
	.llseek = noop_llseek,
};

static int cora_pwm_probe(struct platform_device *platform)
{
	struct cora_pwm_dev *pwm;
	u32 core_id;
	int ret;

	pwm = devm_kzalloc(&platform->dev, sizeof(*pwm), GFP_KERNEL);
	if (!pwm)
		return -ENOMEM;

	pwm->dev = &platform->dev;
	pwm->regs = devm_platform_ioremap_resource(platform, 0);
	if (IS_ERR(pwm->regs))
		return PTR_ERR(pwm->regs);

	core_id = readl(pwm->regs + PWM_CORE_ID_REG);
	if (core_id != CORA_PWM_CORE_ID)
		return dev_err_probe(&platform->dev, -ENODEV,
				     "unexpected PWM core ID 0x%08x\n", core_id);

	pwm->tx_chan = dma_request_chan(&platform->dev, "tx");
	if (IS_ERR(pwm->tx_chan))
		return dev_err_probe(&platform->dev, PTR_ERR(pwm->tx_chan),
				     "failed to request MM2S DMA channel\n");

	if (!dma_has_cap(DMA_CYCLIC, pwm->tx_chan->device->cap_mask)) {
		ret = -EOPNOTSUPP;
		dev_err(&platform->dev, "DMA channel does not support cyclic mode\n");
		goto release_channel;
	}

	pwm->dma_dev = pwm->tx_chan->device->dev;
	pwm->ring_cpu = dma_alloc_coherent(pwm->dma_dev, CORA_PWM_RING_BYTES,
					   &pwm->ring_dma, GFP_KERNEL);
	if (!pwm->ring_cpu) {
		ret = -ENOMEM;
		goto release_channel;
	}

	mutex_init(&pwm->io_lock);
	spin_lock_init(&pwm->state_lock);
	init_waitqueue_head(&pwm->write_wait);
	atomic_set(&pwm->opened, 0);
	pwm->period_ticks = CORA_PWM_DEFAULT_PERIOD_TICKS;
	pwm->config_flags = CORA_PWM_CFG_DITHER;
	cora_pwm_reset_ring(pwm);
	writel(pwm->period_ticks, pwm->regs + PWM_PERIOD_TICKS);
	cora_pwm_disable_output(pwm);

	pwm->miscdev.minor = MISC_DYNAMIC_MINOR;
	pwm->miscdev.name = "cora-pwm0";
	pwm->miscdev.fops = &cora_pwm_fops;
	pwm->miscdev.parent = &platform->dev;
	pwm->miscdev.mode = 0660;

	ret = misc_register(&pwm->miscdev);
	if (ret)
		goto free_ring;

	platform_set_drvdata(platform, pwm);
	ret = sysfs_create_group(&platform->dev.kobj,
				 &cora_pwm_attribute_group);
	if (ret)
		goto deregister_misc;
	dev_info(&platform->dev,
		 "registered /dev/%s, %u-byte periods, %u-period cyclic ring\n",
		 pwm->miscdev.name, CORA_PWM_PERIOD_BYTES, CORA_PWM_RING_PERIODS);

	return 0;

deregister_misc:
	misc_deregister(&pwm->miscdev);
free_ring:
	dma_free_coherent(pwm->dma_dev, CORA_PWM_RING_BYTES,
			  pwm->ring_cpu, pwm->ring_dma);
release_channel:
	dma_release_channel(pwm->tx_chan);
	return ret;
}

static void cora_pwm_remove(struct platform_device *platform)
{
	struct cora_pwm_dev *pwm = platform_get_drvdata(platform);

	sysfs_remove_group(&platform->dev.kobj, &cora_pwm_attribute_group);
	misc_deregister(&pwm->miscdev);
	mutex_lock(&pwm->io_lock);
	cora_pwm_stop_locked(pwm);
	mutex_unlock(&pwm->io_lock);
	dma_free_coherent(pwm->dma_dev, CORA_PWM_RING_BYTES,
			  pwm->ring_cpu, pwm->ring_dma);
	dma_release_channel(pwm->tx_chan);
}

static const struct of_device_id cora_pwm_of_match[] = {
	{ .compatible = "eldden28,cora-pwm-1.0" },
	{ }
};
MODULE_DEVICE_TABLE(of, cora_pwm_of_match);

static struct platform_driver cora_pwm_driver = {
	.probe = cora_pwm_probe,
	.remove = cora_pwm_remove,
	.driver = {
		.name = DRIVER_NAME,
		.of_match_table = cora_pwm_of_match,
	},
};
module_platform_driver(cora_pwm_driver);

MODULE_AUTHOR("eldden28");
MODULE_DESCRIPTION("Cora Z7-10 AXI DMA to signed-16 PWM client");
MODULE_LICENSE("GPL");
