/* SPDX-License-Identifier: GPL-3.0-or-later */
#ifndef INCLUDED_CORA_PWM_SINK_H
#define INCLUDED_CORA_PWM_SINK_H

#include <gnuradio/cora/api.h>
#include <gnuradio/sync_block.h>

#include <cstdint>
#include <memory>
#include <string>

namespace gr {
namespace cora {

class CORA_API pwm_sink : virtual public gr::sync_block
{
public:
    using sptr = std::shared_ptr<pwm_sink>;

    static sptr make(const std::string& device = "/dev/cora-pwm0",
                     std::uint32_t period_ticks = 2000,
                     bool invert = false,
                     bool dither = true);

    virtual std::uint32_t period_bytes() const = 0;
    virtual std::uint32_t ring_periods() const = 0;
};

} // namespace cora
} // namespace gr

#endif
