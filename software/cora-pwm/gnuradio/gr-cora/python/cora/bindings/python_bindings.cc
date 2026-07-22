/* SPDX-License-Identifier: GPL-3.0-or-later */
#include <pybind11/pybind11.h>

namespace py = pybind11;

void bind_pwm_sink(py::module& module);

PYBIND11_MODULE(cora_python, module)
{
    py::module::import("gnuradio.gr");
    bind_pwm_sink(module);
}
