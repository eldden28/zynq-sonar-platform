/* SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef INCLUDED_CORA_PWM_SINK_IMPL_H
#define INCLUDED_CORA_PWM_SINK_IMPL_H

#include <gnuradio/cora/pwm_sink.h>

#include <atomic>
#include <cstdint>
#include <string>
#include <vector>

namespace gr {
namespace cora {

class pwm_sink_impl : public pwm_sink
{
public:
    pwm_sink_impl(const std::string& device,
                  std::uint32_t period_ticks,
                  bool invert,
                  bool dither);
    ~pwm_sink_impl() override;

    bool start() override;
    bool stop() override;

    int work(int noutput_items,
             gr_vector_const_void_star& input_items,
             gr_vector_void_star& output_items) override;

    std::uint32_t period_bytes() const override { return d_period_bytes; }
    std::uint32_t ring_periods() const override { return d_ring_periods; }

private:
    bool write_period(const std::int16_t* samples);
    void close_device();

    std::string d_device;
    std::uint32_t d_period_ticks;
    bool d_invert;
    bool d_dither;
    int d_fd = -1;
    std::uint32_t d_period_bytes = 0;
    std::uint32_t d_ring_periods = 0;
    std::vector<std::int16_t> d_pending;
    std::size_t d_pending_items = 0;
    std::atomic<bool> d_stopping{ false };
};

} // namespace cora
} // namespace gr

#endif
