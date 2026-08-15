#!/usr/bin/env python3
"""Run a minimal, hardware-independent GNU Radio flowgraph."""

from gnuradio import blocks, gr


def main() -> None:
    flowgraph = gr.top_block()
    source = blocks.vector_source_f([1, 2, 3, 4], False)
    scale = blocks.multiply_const_ff(2.0)
    sink = blocks.vector_sink_f()

    flowgraph.connect(source, scale, sink)
    flowgraph.run()

    result = list(sink.data())
    expected = [2.0, 4.0, 6.0, 8.0]
    print(f"Result: {result}")
    if result != expected:
        raise SystemExit(f"GNU Radio smoke test: FAIL (expected {expected})")
    print("GNU Radio smoke test: PASS")


if __name__ == "__main__":
    main()
