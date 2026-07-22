/* SPDX-License-Identifier: GPL-2.0 WITH Linux-syscall-note */
#ifndef CORA_PWM_IOCTL_H
#define CORA_PWM_IOCTL_H

#include <linux/ioctl.h>
#include <linux/types.h>

#define CORA_PWM_ABI_VERSION 1U
#define CORA_PWM_CORE_ID 0x50574d31U

#define CORA_PWM_CFG_INVERT (1U << 0)
#define CORA_PWM_CFG_DITHER (1U << 1)

#define CORA_PWM_STATUS_RUNNING (1U << 0)
#define CORA_PWM_STATUS_OPEN (1U << 1)

struct cora_pwm_info {
	__u32 abi_version;
	__u32 core_id;
	__u32 core_version;
	__u32 pwm_clock_hz;
	__u32 period_bytes;
	__u32 ring_periods;
	__u32 sample_bytes;
	__u32 reserved;
};

struct cora_pwm_config {
	__u32 period_ticks;
	__u32 flags;
};

struct cora_pwm_status {
	__u32 flags;
	__u32 queued_periods;
	__u32 free_periods;
	__u32 pwm_fifo_level;
	__u32 active_period;
	__u32 active_duty;
	__u32 pwm_underruns;
	__u32 reserved;
	__u64 dma_periods;
	__u64 driver_underruns;
	__u64 accepted_samples;
};

#define CORA_PWM_IOC_MAGIC 'P'
#define CORA_PWM_IOC_GET_INFO _IOR(CORA_PWM_IOC_MAGIC, 0x00, struct cora_pwm_info)
#define CORA_PWM_IOC_SET_CONFIG _IOW(CORA_PWM_IOC_MAGIC, 0x01, struct cora_pwm_config)
#define CORA_PWM_IOC_GET_STATUS _IOR(CORA_PWM_IOC_MAGIC, 0x02, struct cora_pwm_status)
#define CORA_PWM_IOC_STOP _IO(CORA_PWM_IOC_MAGIC, 0x03)
#define CORA_PWM_IOC_CLEAR_STATS _IO(CORA_PWM_IOC_MAGIC, 0x04)

#endif
