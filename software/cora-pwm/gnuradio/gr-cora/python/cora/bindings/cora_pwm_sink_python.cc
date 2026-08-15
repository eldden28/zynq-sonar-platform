/* SPDX-License-Identifier: GPL-3.0-or-later */
#include <gnuradio/cora/pwm_sink.h>

#include <pybind11/pybind11.h>

namespace py = pybind11;

void bind_pwm_sink(py::module& module)
{
    using pwm_sink = gr::cora::pwm_sink;

    py::class_<pwm_sink,
               gr::sync_block,
               gr::block,
               gr::basic_block,
               std::shared_ptr<pwm_sink>>(module, "pwm_sink")
        .def(py::init(&pwm_sink::make),
             py::arg("device") = "/dev/cora-pwm0",
             py::arg("period_ticks") = 2000,
             py::arg("invert") = false,
             py::arg("dither") = true)
        .def("period_bytes", &pwm_sink::period_bytes)
        .def("ring_periods", &pwm_sink::ring_periods);
}
