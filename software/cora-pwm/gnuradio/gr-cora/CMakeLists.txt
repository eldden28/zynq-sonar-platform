cmake_minimum_required(VERSION 3.16)
project(gr-cora VERSION 1.0.0 LANGUAGES CXX)

include(GNUInstallDirs)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_CXX_STANDARD_REQUIRED ON)
set(CMAKE_CXX_EXTENSIONS OFF)

find_package(Gnuradio REQUIRED COMPONENTS runtime)

# GNU Radio's Yocto toolchain exports these legacy variables for the target
# Python installation.  Do not use CMake's Python3::Module target here: its
# interpreter is native (needed while building), so it can select native
# headers and an x86-64 extension suffix during a cross build.
if(NOT PYTHON_INCLUDE_DIR OR NOT PYTHON_LIBRARY)
    find_package(PythonLibs REQUIRED)
endif()

# Only pybind11's headers are required.  Its CMake package probes the native
# interpreter, which is intentionally a different architecture in Yocto.
find_path(PYBIND11_INCLUDE_DIR pybind11/pybind11.h REQUIRED)

set(CORA_PWM_UAPI_DIR "${CMAKE_CURRENT_SOURCE_DIR}/../../include"
    CACHE PATH "Directory containing cora_pwm_ioctl.h")

add_library(gnuradio-cora SHARED
    lib/cora_pwm_sink_impl.cc
)
target_include_directories(gnuradio-cora
    PUBLIC
        $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/include>
        $<INSTALL_INTERFACE:${CMAKE_INSTALL_INCLUDEDIR}>
    PRIVATE
        ${CORA_PWM_UAPI_DIR}
)
target_link_libraries(gnuradio-cora PUBLIC gnuradio::gnuradio-runtime)
set_target_properties(gnuradio-cora PROPERTIES
    VERSION ${PROJECT_VERSION}
    SOVERSION 1
)

add_library(cora_python MODULE
    python/cora/bindings/cora_pwm_sink_python.cc
    python/cora/bindings/python_bindings.cc
)
target_include_directories(cora_python PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/include
    ${PYTHON_INCLUDE_DIR}
    ${PYBIND11_INCLUDE_DIR}
)
target_link_libraries(cora_python PRIVATE
    gnuradio-cora
    ${PYTHON_LIBRARY}
)
set_target_properties(cora_python PROPERTIES
    PREFIX ""
    SUFFIX ".so"
    CXX_VISIBILITY_PRESET hidden
)

if(NOT DEFINED GR_PYTHON_DIR)
    set(GR_PYTHON_DIR "${CMAKE_INSTALL_LIBDIR}/python3/site-packages")
endif()

install(TARGETS gnuradio-cora
    LIBRARY DESTINATION ${CMAKE_INSTALL_LIBDIR}
)
install(DIRECTORY include/gnuradio
    DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
)
install(FILES ${CORA_PWM_UAPI_DIR}/cora_pwm_ioctl.h
    DESTINATION ${CMAKE_INSTALL_INCLUDEDIR}
)
install(TARGETS cora_python
    LIBRARY DESTINATION ${GR_PYTHON_DIR}/gnuradio/cora
)
install(FILES
    python/cora/__init__.py
    python/cora/adc.py
    DESTINATION ${GR_PYTHON_DIR}/gnuradio/cora
)
install(FILES
    grc/cora_pwm_sink.block.yml
    grc/cora_iio_adc_source.block.yml
    DESTINATION ${CMAKE_INSTALL_DATADIR}/gnuradio/grc/blocks
)
install(PROGRAMS
    examples/cora-pwm-smoke
    examples/cora-adc-smoke
    examples/cora-adc-to-pwm
    DESTINATION ${CMAKE_INSTALL_BINDIR}
)
