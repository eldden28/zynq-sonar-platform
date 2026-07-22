/* SPDX-License-Identifier: GPL-3.0-or-later */
#include "cora_pwm_sink_impl.h"

#include <gnuradio/io_signature.h>

#include <algorithm>
#include <cerrno>
#include <cstring>
#include <fcntl.h>
#include <poll.h>
#include <stdexcept>
#include <sys/ioctl.h>
#include <unistd.h>

#include "cora_pwm_ioctl.h"

namespace gr {
namespace cora {

pwm_sink::sptr pwm_sink::make(const std::string& device,
                              std::uint32_t period_ticks,
                              bool invert,
                              bool dither)
{
    return gnuradio::make_block_sptr<pwm_sink_impl>(
        device, period_ticks, invert, dither);
}

pwm_sink_impl::pwm_sink_impl(const std::string& device,
                             std::uint32_t period_ticks,
                             bool invert,
                             bool dither)
    : gr::sync_block("cora_pwm_sink",
                     gr::io_signature::make(1, 1, sizeof(std::int16_t)),
                     gr::io_signature::make(0, 0, 0)),
      d_device(device),
      d_period_ticks(period_ticks),
      d_invert(invert),
      d_dither(dither)
{
    if (period_ticks < 2 || period_ticks > 65535)
        throw std::invalid_argument("period_ticks must be between 2 and 65535");
}

pwm_sink_impl::~pwm_sink_impl() { close_device(); }

bool pwm_sink_impl::start()
{
    cora_pwm_info info{};
    cora_pwm_config config{};

    d_stopping.store(false);
    d_fd = ::open(d_device.c_str(), O_WRONLY | O_NONBLOCK | O_CLOEXEC);
    if (d_fd < 0)
        throw std::runtime_error("cannot open " + d_device + ": " +
                                 std::strerror(errno));

    if (::ioctl(d_fd, CORA_PWM_IOC_GET_INFO, &info) < 0) {
        const std::string message = "CORA_PWM_IOC_GET_INFO failed: " +
                                    std::string(std::strerror(errno));
        close_device();
        throw std::runtime_error(message);
    }
    if (info.abi_version != CORA_PWM_ABI_VERSION || info.core_id != CORA_PWM_CORE_ID ||
        info.sample_bytes != sizeof(std::int16_t) ||
        info.period_bytes == 0 || info.period_bytes % sizeof(std::int16_t)) {
        close_device();
        throw std::runtime_error("incompatible cora-pwm kernel ABI or FPGA core");
    }

    config.period_ticks = d_period_ticks;
    config.flags = (d_invert ? CORA_PWM_CFG_INVERT : 0) |
                   (d_dither ? CORA_PWM_CFG_DITHER : 0);
    if (::ioctl(d_fd, CORA_PWM_IOC_SET_CONFIG, &config) < 0) {
        const std::string message = "CORA_PWM_IOC_SET_CONFIG failed: " +
                                    std::string(std::strerror(errno));
        close_device();
        throw std::runtime_error(message);
    }

    d_period_bytes = info.period_bytes;
    d_ring_periods = info.ring_periods;
    d_pending.assign(d_period_bytes / sizeof(std::int16_t), 0);
    d_pending_items = 0;

    return true;
}

bool pwm_sink_impl::stop()
{
    d_stopping.store(true);
    if (d_fd >= 0)
        ::ioctl(d_fd, CORA_PWM_IOC_STOP);
    close_device();
    d_pending_items = 0;
    return true;
}

void pwm_sink_impl::close_device()
{
    if (d_fd >= 0) {
        ::close(d_fd);
        d_fd = -1;
    }
}

bool pwm_sink_impl::write_period(const std::int16_t* samples)
{
    const auto bytes = static_cast<std::size_t>(d_period_bytes);

    while (!d_stopping.load()) {
        const ssize_t result = ::write(d_fd, samples, bytes);
        if (result == static_cast<ssize_t>(bytes))
            return true;
        if (result < 0 && errno == EINTR)
            continue;
        if (result < 0 && (errno == EAGAIN || errno == EWOULDBLOCK || errno == EPIPE)) {
            pollfd descriptor{ d_fd, POLLOUT, 0 };
            const int poll_result = ::poll(&descriptor, 1, 100);
            if (poll_result < 0 && errno != EINTR)
                throw std::runtime_error("poll on " + d_device + " failed: " +
                                         std::strerror(errno));
            continue;
        }
        if (result >= 0)
            throw std::runtime_error("short write to " + d_device);
        throw std::runtime_error("write to " + d_device + " failed: " +
                                 std::strerror(errno));
    }

    return false;
}

int pwm_sink_impl::work(int noutput_items,
                        gr_vector_const_void_star& input_items,
                        gr_vector_void_star& output_items)
{
    const auto* input = static_cast<const std::int16_t*>(input_items[0]);
    std::size_t consumed = 0;

    while (consumed < static_cast<std::size_t>(noutput_items)) {
        const std::size_t capacity = d_pending.size() - d_pending_items;
        const std::size_t available = static_cast<std::size_t>(noutput_items) - consumed;
        const std::size_t copy_items = std::min(capacity, available);

        std::copy_n(input + consumed, copy_items, d_pending.data() + d_pending_items);
        consumed += copy_items;
        d_pending_items += copy_items;

        if (d_pending_items == d_pending.size()) {
            if (!write_period(d_pending.data()))
                return -1;
            d_pending_items = 0;
        }
    }

    return noutput_items;
}

} // namespace cora
} // namespace gr
