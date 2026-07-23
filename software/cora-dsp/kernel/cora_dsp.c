// SPDX-License-Identifier: GPL-2.0
#include <linux/atomic.h>
#include <linux/completion.h>
#include <linux/dmaengine.h>
#include <linux/dma-mapping.h>
#include <linux/fs.h>
#include <linux/io.h>
#include <linux/ktime.h>
#include <linux/miscdevice.h>
#include <linux/module.h>
#include <linux/mutex.h>
#include <linux/of.h>
#include <linux/platform_device.h>
#include <linux/slab.h>
#include <linux/uaccess.h>

#include "cora_dsp_ioctl.h"

#define DRIVER_NAME "cora_dsp"

#define DSP_LOW_BIN 0x00
#define DSP_HIGH_BIN 0x04
#define DSP_COMMAND 0x08
#define DSP_STATUS_BIN 0x0c
#define DSP_FRAMES_LO 0x10
#define DSP_FRAMES_HI 0x14
#define DSP_SAMPLES_LO 0x18
#define DSP_SAMPLES_HI 0x1c
#define DSP_FILTERED_BINS_LO 0x20
#define DSP_FILTERED_BINS_HI 0x24
#define DSP_CORE_ID_REG 0x28
#define DSP_CORE_VERSION_REG 0x2c

#define CORA_DSP_USER_BYTES (CORA_DSP_FFT_LENGTH * sizeof(s16))
#define CORA_DSP_DMA_BYTES (CORA_DSP_FFT_LENGTH * sizeof(u32))
#define CORA_DSP_TIMEOUT_MS 1000

struct cora_dsp_dev {
	struct device *dev;
	void __iomem *regs;
	struct dma_chan *tx_chan;
	struct dma_chan *rx_chan;
	struct device *dma_dev;
	u32 *input_dma_cpu;
	dma_addr_t input_dma;
	u32 *output_dma_cpu;
	dma_addr_t output_dma;
	s16 *input_samples;
	s16 *output_samples;
	struct completion tx_complete;
	struct completion rx_complete;
	struct miscdevice miscdev;
	struct mutex lock;
	atomic_t opened;
	u32 low_bin;
	u32 high_bin;
	bool result_ready;
	u64 completed_frames;
	u64 failed_frames;
	u64 accelerator_time_ns;
};

static u64 cora_dsp_read_counter(struct cora_dsp_dev *dsp, u32 low_reg,
				 u32 high_reg)
{
	u32 high_before;
	u32 low;
	u32 high_after;

	do {
		high_before = readl(dsp->regs + high_reg);
		low = readl(dsp->regs + low_reg);
		high_after = readl(dsp->regs + high_reg);
	} while (high_before != high_after);

	return ((u64)high_after << 32) | low;
}

static void cora_dsp_dma_complete(void *argument)
{
	complete(argument);
}

static int cora_dsp_wait(struct cora_dsp_dev *dsp,
			 struct completion *completion)
{
	long ret;

	ret = wait_for_completion_interruptible_timeout(
		completion, msecs_to_jiffies(CORA_DSP_TIMEOUT_MS));
	if (ret > 0)
		return 0;
	if (!ret)
		return -ETIMEDOUT;
	return ret;
}

static int cora_dsp_process(struct cora_dsp_dev *dsp)
{
	struct dma_async_tx_descriptor *rx_descriptor;
	struct dma_async_tx_descriptor *tx_descriptor;
	dma_cookie_t rx_cookie;
	dma_cookie_t tx_cookie;
	ktime_t start;
	u32 sample;
	int ret;

	for (sample = 0; sample < CORA_DSP_FFT_LENGTH; sample++)
		dsp->input_dma_cpu[sample] = (u16)dsp->input_samples[sample];
	memset(dsp->output_dma_cpu, 0, CORA_DSP_DMA_BYTES);

	reinit_completion(&dsp->rx_complete);
	reinit_completion(&dsp->tx_complete);

	rx_descriptor = dmaengine_prep_slave_single(
		dsp->rx_chan, dsp->output_dma, CORA_DSP_DMA_BYTES,
		DMA_DEV_TO_MEM, DMA_PREP_INTERRUPT | DMA_CTRL_ACK);
	if (!rx_descriptor)
		return -EIO;
	rx_descriptor->callback = cora_dsp_dma_complete;
	rx_descriptor->callback_param = &dsp->rx_complete;

	tx_descriptor = dmaengine_prep_slave_single(
		dsp->tx_chan, dsp->input_dma, CORA_DSP_DMA_BYTES,
		DMA_MEM_TO_DEV, DMA_PREP_INTERRUPT | DMA_CTRL_ACK);
	if (!tx_descriptor)
		return -EIO;
	tx_descriptor->callback = cora_dsp_dma_complete;
	tx_descriptor->callback_param = &dsp->tx_complete;

	rx_cookie = dmaengine_submit(rx_descriptor);
	if (dma_submit_error(rx_cookie))
		return dma_submit_error(rx_cookie);
	tx_cookie = dmaengine_submit(tx_descriptor);
	if (dma_submit_error(tx_cookie)) {
		dmaengine_terminate_sync(dsp->rx_chan);
		return dma_submit_error(tx_cookie);
	}

	start = ktime_get();
	dma_async_issue_pending(dsp->rx_chan);
	dma_async_issue_pending(dsp->tx_chan);

	ret = cora_dsp_wait(dsp, &dsp->rx_complete);
	if (!ret)
		ret = cora_dsp_wait(dsp, &dsp->tx_complete);
	if (ret) {
		enum dma_status tx_status;
		enum dma_status rx_status;
		u64 hardware_frames;
		u64 hardware_samples;

		tx_status = dmaengine_tx_status(
			dsp->tx_chan, tx_cookie, NULL);
		rx_status = dmaengine_tx_status(
			dsp->rx_chan, rx_cookie, NULL);
		hardware_frames = cora_dsp_read_counter(
			dsp, DSP_FRAMES_LO, DSP_FRAMES_HI);
		hardware_samples = cora_dsp_read_counter(
			dsp, DSP_SAMPLES_LO, DSP_SAMPLES_HI);
		dev_err(dsp->dev,
			"transaction failed %d: tx_completion=%d tx_status=%d "
			"rx_completion=%d rx_status=%d filter_bin=%u "
			"filter_frames=%llu filter_samples=%llu\n",
			ret, completion_done(&dsp->tx_complete), tx_status,
			completion_done(&dsp->rx_complete), rx_status,
			readl(dsp->regs + DSP_STATUS_BIN),
			hardware_frames, hardware_samples);
		dmaengine_terminate_sync(dsp->tx_chan);
		dmaengine_terminate_sync(dsp->rx_chan);
		dsp->failed_frames++;
		return ret;
	}

	dsp->accelerator_time_ns += ktime_to_ns(ktime_sub(ktime_get(), start));
	dsp->completed_frames++;

	for (sample = 0; sample < CORA_DSP_FFT_LENGTH; sample++)
		dsp->output_samples[sample] =
			(s16)(dsp->output_dma_cpu[sample] & 0xffff);
	dsp->result_ready = true;

	return 0;
}

static int cora_dsp_open(struct inode *inode, struct file *file)
{
	struct miscdevice *misc = file->private_data;
	struct cora_dsp_dev *dsp =
		container_of(misc, struct cora_dsp_dev, miscdev);

	if (atomic_cmpxchg(&dsp->opened, 0, 1))
		return -EBUSY;
	file->private_data = dsp;
	dsp->result_ready = false;
	return nonseekable_open(inode, file);
}

static int cora_dsp_release(struct inode *inode, struct file *file)
{
	struct cora_dsp_dev *dsp = file->private_data;

	/*
	 * cora_dsp_write() is synchronous: it does not return until both DMA
	 * directions have completed, or until its error path has terminated
	 * them.  Resetting the AXI DMA channels again here can disturb their
	 * completed-descriptor cleanup and leave the next opener unable to
	 * start its first transfer.
	 */
	mutex_lock(&dsp->lock);
	dsp->result_ready = false;
	mutex_unlock(&dsp->lock);
	atomic_set(&dsp->opened, 0);
	return 0;
}

static ssize_t cora_dsp_write(struct file *file, const char __user *buffer,
			      size_t count, loff_t *position)
{
	struct cora_dsp_dev *dsp = file->private_data;
	int ret;

	if (count != CORA_DSP_USER_BYTES)
		return -EINVAL;
	if (copy_from_user(dsp->input_samples, buffer, CORA_DSP_USER_BYTES))
		return -EFAULT;

	mutex_lock(&dsp->lock);
	dsp->result_ready = false;
	ret = cora_dsp_process(dsp);
	mutex_unlock(&dsp->lock);

	return ret ? ret : count;
}

static ssize_t cora_dsp_read(struct file *file, char __user *buffer,
			     size_t count, loff_t *position)
{
	struct cora_dsp_dev *dsp = file->private_data;
	ssize_t ret;

	if (count < CORA_DSP_USER_BYTES)
		return -EINVAL;

	mutex_lock(&dsp->lock);
	if (!dsp->result_ready) {
		ret = -EAGAIN;
	} else if (copy_to_user(buffer, dsp->output_samples,
				CORA_DSP_USER_BYTES)) {
		ret = -EFAULT;
	} else {
		dsp->result_ready = false;
		ret = CORA_DSP_USER_BYTES;
	}
	mutex_unlock(&dsp->lock);
	return ret;
}

static long cora_dsp_ioctl(struct file *file, unsigned int command,
			   unsigned long argument)
{
	struct cora_dsp_dev *dsp = file->private_data;
	void __user *user_argument = (void __user *)argument;

	switch (command) {
	case CORA_DSP_IOC_GET_INFO: {
		struct cora_dsp_info info = {
			.abi_version = CORA_DSP_ABI_VERSION,
			.core_id = readl(dsp->regs + DSP_CORE_ID_REG),
			.core_version = readl(dsp->regs + DSP_CORE_VERSION_REG),
			.fft_length = CORA_DSP_FFT_LENGTH,
			.sample_bytes = sizeof(s16),
			.dma_sample_bytes = sizeof(u32),
		};

		return copy_to_user(user_argument, &info, sizeof(info)) ?
			-EFAULT : 0;
	}
	case CORA_DSP_IOC_SET_FILTER: {
		struct cora_dsp_filter_config config;

		if (copy_from_user(&config, user_argument, sizeof(config)))
			return -EFAULT;
		if (config.low_bin > config.high_bin ||
		    config.high_bin > CORA_DSP_FFT_LENGTH / 2)
			return -EINVAL;

		mutex_lock(&dsp->lock);
		dsp->low_bin = config.low_bin;
		dsp->high_bin = config.high_bin;
		writel(dsp->low_bin, dsp->regs + DSP_LOW_BIN);
		writel(dsp->high_bin, dsp->regs + DSP_HIGH_BIN);
		mutex_unlock(&dsp->lock);
		return 0;
	}
	case CORA_DSP_IOC_GET_STATUS: {
		struct cora_dsp_status status;

		mutex_lock(&dsp->lock);
		status = (struct cora_dsp_status) {
			.low_bin = dsp->low_bin,
			.high_bin = dsp->high_bin,
			.result_ready = dsp->result_ready,
			.completed_frames = dsp->completed_frames,
			.failed_frames = dsp->failed_frames,
			.accelerator_time_ns = dsp->accelerator_time_ns,
			.filtered_bins = cora_dsp_read_counter(
				dsp, DSP_FILTERED_BINS_LO,
				DSP_FILTERED_BINS_HI),
		};
		mutex_unlock(&dsp->lock);
		return copy_to_user(user_argument, &status, sizeof(status)) ?
			-EFAULT : 0;
	}
	case CORA_DSP_IOC_CLEAR_STATS:
		mutex_lock(&dsp->lock);
		dsp->completed_frames = 0;
		dsp->failed_frames = 0;
		dsp->accelerator_time_ns = 0;
		writel(1, dsp->regs + DSP_COMMAND);
		mutex_unlock(&dsp->lock);
		return 0;
	default:
		return -ENOTTY;
	}
}

static const struct file_operations cora_dsp_fops = {
	.owner = THIS_MODULE,
	.open = cora_dsp_open,
	.release = cora_dsp_release,
	.read = cora_dsp_read,
	.write = cora_dsp_write,
	.unlocked_ioctl = cora_dsp_ioctl,
#ifdef CONFIG_COMPAT
	.compat_ioctl = cora_dsp_ioctl,
#endif
	.llseek = noop_llseek,
};

static int cora_dsp_probe(struct platform_device *platform)
{
	struct cora_dsp_dev *dsp;
	u32 core_id;
	int ret;

	dsp = devm_kzalloc(&platform->dev, sizeof(*dsp), GFP_KERNEL);
	if (!dsp)
		return -ENOMEM;
	dsp->dev = &platform->dev;

	dsp->regs = devm_platform_ioremap_resource(platform, 0);
	if (IS_ERR(dsp->regs))
		return PTR_ERR(dsp->regs);
	core_id = readl(dsp->regs + DSP_CORE_ID_REG);
	if (core_id != CORA_DSP_CORE_ID)
		return dev_err_probe(&platform->dev, -ENODEV,
				     "unexpected DSP core ID 0x%08x\n", core_id);

	dsp->tx_chan = dma_request_chan(&platform->dev, "tx");
	if (IS_ERR(dsp->tx_chan))
		return dev_err_probe(&platform->dev, PTR_ERR(dsp->tx_chan),
				     "failed to request MM2S DMA channel\n");
	dsp->rx_chan = dma_request_chan(&platform->dev, "rx");
	if (IS_ERR(dsp->rx_chan)) {
		ret = dev_err_probe(&platform->dev, PTR_ERR(dsp->rx_chan),
				    "failed to request S2MM DMA channel\n");
		goto release_tx;
	}

	if (dsp->tx_chan->device->dev != dsp->rx_chan->device->dev) {
		ret = -EINVAL;
		dev_err(&platform->dev, "DMA channels do not share one device\n");
		goto release_rx;
	}
	dsp->dma_dev = dsp->tx_chan->device->dev;

	dsp->input_dma_cpu = dma_alloc_coherent(
		dsp->dma_dev, CORA_DSP_DMA_BYTES, &dsp->input_dma, GFP_KERNEL);
	if (!dsp->input_dma_cpu) {
		ret = -ENOMEM;
		goto release_rx;
	}
	dsp->output_dma_cpu = dma_alloc_coherent(
		dsp->dma_dev, CORA_DSP_DMA_BYTES, &dsp->output_dma, GFP_KERNEL);
	if (!dsp->output_dma_cpu) {
		ret = -ENOMEM;
		goto free_input_dma;
	}

	dsp->input_samples = devm_kmalloc(
		&platform->dev, CORA_DSP_USER_BYTES, GFP_KERNEL);
	dsp->output_samples = devm_kmalloc(
		&platform->dev, CORA_DSP_USER_BYTES, GFP_KERNEL);
	if (!dsp->input_samples || !dsp->output_samples) {
		ret = -ENOMEM;
		goto free_output_dma;
	}

	mutex_init(&dsp->lock);
	init_completion(&dsp->tx_complete);
	init_completion(&dsp->rx_complete);
	atomic_set(&dsp->opened, 0);
	dsp->low_bin = 0;
	dsp->high_bin = CORA_DSP_FFT_LENGTH / 2;
	writel(dsp->low_bin, dsp->regs + DSP_LOW_BIN);
	writel(dsp->high_bin, dsp->regs + DSP_HIGH_BIN);
	writel(1, dsp->regs + DSP_COMMAND);

	dsp->miscdev.minor = MISC_DYNAMIC_MINOR;
	dsp->miscdev.name = "cora-dsp0";
	dsp->miscdev.fops = &cora_dsp_fops;
	dsp->miscdev.parent = &platform->dev;
	dsp->miscdev.mode = 0660;

	ret = misc_register(&dsp->miscdev);
	if (ret)
		goto free_output_dma;
	platform_set_drvdata(platform, dsp);
	dev_info(&platform->dev,
		 "registered /dev/%s, %u-point FFT/filter/IFFT accelerator\n",
		 dsp->miscdev.name, CORA_DSP_FFT_LENGTH);
	return 0;

free_output_dma:
	dma_free_coherent(dsp->dma_dev, CORA_DSP_DMA_BYTES,
			  dsp->output_dma_cpu, dsp->output_dma);
free_input_dma:
	dma_free_coherent(dsp->dma_dev, CORA_DSP_DMA_BYTES,
			  dsp->input_dma_cpu, dsp->input_dma);
release_rx:
	dma_release_channel(dsp->rx_chan);
release_tx:
	dma_release_channel(dsp->tx_chan);
	return ret;
}

static void cora_dsp_remove(struct platform_device *platform)
{
	struct cora_dsp_dev *dsp = platform_get_drvdata(platform);

	misc_deregister(&dsp->miscdev);
	dmaengine_terminate_sync(dsp->tx_chan);
	dmaengine_terminate_sync(dsp->rx_chan);
	dma_free_coherent(dsp->dma_dev, CORA_DSP_DMA_BYTES,
			  dsp->output_dma_cpu, dsp->output_dma);
	dma_free_coherent(dsp->dma_dev, CORA_DSP_DMA_BYTES,
			  dsp->input_dma_cpu, dsp->input_dma);
	dma_release_channel(dsp->rx_chan);
	dma_release_channel(dsp->tx_chan);
}

static const struct of_device_id cora_dsp_of_match[] = {
	{ .compatible = "eldden28,cora-dsp-1.0" },
	{ }
};
MODULE_DEVICE_TABLE(of, cora_dsp_of_match);

static struct platform_driver cora_dsp_driver = {
	.probe = cora_dsp_probe,
	.remove = cora_dsp_remove,
	.driver = {
		.name = DRIVER_NAME,
		.of_match_table = cora_dsp_of_match,
	},
};
module_platform_driver(cora_dsp_driver);

MODULE_AUTHOR("eldden28");
MODULE_DESCRIPTION("Cora 512-point DMA FFT/filter/IFFT accelerator");
MODULE_LICENSE("GPL");
