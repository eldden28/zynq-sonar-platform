/* SPDX-License-Identifier: GPL-2.0 WITH Linux-syscall-note */
#ifndef CORA_DSP_IOCTL_H
#define CORA_DSP_IOCTL_H

#include <linux/ioctl.h>
#include <linux/types.h>

#define CORA_DSP_ABI_VERSION 2U
#define CORA_DSP_CORE_ID 0x44535031U
#define CORA_DSP_FFT_LENGTH 512U
#define CORA_DSP_CAP_AXIS_ROUTER (1U << 0)
#define CORA_DSP_CAP_SAMPLE_PACING (1U << 1)

#define CORA_DSP_PATH_LOOPBACK 0U
#define CORA_DSP_PATH_ACCELERATOR 1U

struct cora_dsp_info {
	__u32 abi_version;
	__u32 core_id;
	__u32 core_version;
	__u32 fft_length;
	__u32 sample_bytes;
	__u32 dma_sample_bytes;
	__u32 sample_rate;
	__u32 capabilities;
};

struct cora_dsp_filter_config {
	__u32 low_bin;
	__u32 high_bin;
};

struct cora_dsp_status {
	__u32 low_bin;
	__u32 high_bin;
	__u32 result_ready;
	__u32 reserved;
	__u64 completed_frames;
	__u64 failed_frames;
	__u64 accelerator_time_ns;
	__u64 filtered_bins;
};

struct cora_dsp_path_config {
	__u32 path;
	__u32 sample_rate;
};

struct cora_dsp_acomms_status {
	__u32 path;
	__u32 sample_rate;
	__u32 clock_hz;
	__u32 sample_divider;
	__u64 samples;
	__u64 frames;
};

#define CORA_DSP_IOC_MAGIC 'D'
#define CORA_DSP_IOC_GET_INFO \
	_IOR(CORA_DSP_IOC_MAGIC, 0x00, struct cora_dsp_info)
#define CORA_DSP_IOC_SET_FILTER \
	_IOW(CORA_DSP_IOC_MAGIC, 0x01, struct cora_dsp_filter_config)
#define CORA_DSP_IOC_GET_STATUS \
	_IOR(CORA_DSP_IOC_MAGIC, 0x02, struct cora_dsp_status)
#define CORA_DSP_IOC_CLEAR_STATS _IO(CORA_DSP_IOC_MAGIC, 0x03)
#define CORA_DSP_IOC_SET_PATH \
	_IOW(CORA_DSP_IOC_MAGIC, 0x04, struct cora_dsp_path_config)
#define CORA_DSP_IOC_GET_ACOMMS_STATUS \
	_IOR(CORA_DSP_IOC_MAGIC, 0x05, struct cora_dsp_acomms_status)

#endif
