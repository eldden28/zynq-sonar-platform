`timescale 1ns/1ps

// AXI4-Stream signed-16 PWM sink.
//
// The core consumes one sample per PWM period and maps signed input onto duty:
//
//   -32768 ->   0%
//        0 ->  50%
//    32767 -> 100%
//
// Keep this core and its AXIS input in the PWM clock domain. When the producer
// uses another clock, place an AXIS clock converter or asynchronous AXIS FIFO
// before S_AXIS.
module axis_pwm_s16 #(
    parameter integer DEFAULT_PERIOD_TICKS = 2000,
    parameter integer FIFO_DEPTH = 64,
    parameter integer PERIOD_WIDTH = 16
) (
    input  logic        aclk,
    input  logic        aresetn,

    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TDATA" *)
    input  logic [15:0] s_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TVALID" *)
    input  logic        s_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TREADY" *)
    output logic        s_axis_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TLAST" *)
    input  logic        s_axis_tlast,

    // A new period is applied together with the next scaled sample at a PWM
    // boundary. Values are clamped to the PERIOD_WIDTH-supported range.
    input  logic        cfg_enable,
    input  logic        cfg_invert,
    input  logic        cfg_dither_enable,
    input  logic [31:0] cfg_period_ticks,
    input  logic        clear_status,

    output logic        pwm_out,
    output logic        pwm_period_start,

    output logic [31:0] status_period_ticks,
    output logic [31:0] status_duty_ticks,
    output logic [31:0] status_fifo_level,
    output logic [31:0] status_underrun_count,
    output logic [63:0] status_sample_count
);

    localparam integer POINTER_WIDTH = $clog2(FIFO_DEPTH);
    localparam integer COUNT_WIDTH = $clog2(FIFO_DEPTH + 1);
    localparam logic [31:0] MAX_PERIOD_TICKS =
        (32'h0000_0001 << PERIOD_WIDTH) - 1'b1;

    logic [15:0] fifo_memory [0:FIFO_DEPTH-1];
    logic [POINTER_WIDTH-1:0] write_pointer;
    logic [POINTER_WIDTH-1:0] read_pointer;
    logic [COUNT_WIDTH-1:0] fifo_count;

    logic [PERIOD_WIDTH-1:0] period_active;
    logic [PERIOD_WIDTH-1:0] period_ticks_remaining;
    logic [PERIOD_WIDTH-1:0] duty_active;
    logic [PERIOD_WIDTH-1:0] high_ticks_remaining;
    logic period_boundary_pending;
    logic [15:0] dither_remainder;
    logic [31:0] sample_count_low;
    logic [31:0] sample_count_high;

    logic period_wrap;
    logic stream_push;
    logic fifo_prefetch;
    logic fifo_store;
    logic pipeline_busy;
    logic pwm_raw_registered;
    logic [PERIOD_WIDTH-1:0] sanitized_period;

    // Scaler stage 1: snapshot one FIFO sample and its configuration.
    logic        scale_valid_1;
    logic [15:0] scale_level_1;
    logic [PERIOD_WIDTH-1:0] scale_period_1;
    logic [15:0] scale_addend_1;
    logic        scale_dither_1;
    logic        scale_minimum_1;
    logic        scale_maximum_1;

    // Stage 2: registered 16x16 product, inferred as one DSP48.
    logic        scale_valid_2;
    logic [31:0] scale_product_2;
    logic [PERIOD_WIDTH-1:0] scale_period_2;
    logic [15:0] scale_addend_2;
    logic        scale_dither_2;
    logic        scale_minimum_2;
    logic        scale_maximum_2;

    // Stage 3: add fractional remainder or half an LSB for rounding.
    logic        scale_valid_3;
    logic [31:0] scale_total_3;
    logic [PERIOD_WIDTH-1:0] scale_period_3;
    logic        scale_dither_3;
    logic        scale_minimum_3;
    logic        scale_maximum_3;

    // Completed values wait here until the next PWM boundary.
    logic        duty_pending_valid;
    logic [PERIOD_WIDTH-1:0] duty_pending;
    logic [PERIOD_WIDTH-1:0] period_pending;
    logic [15:0] remainder_pending;

    initial begin
        if (PERIOD_WIDTH < 2 || PERIOD_WIDTH > 31) begin
            $error("PERIOD_WIDTH must be between 2 and 31");
        end
        if (DEFAULT_PERIOD_TICKS < 2 ||
            DEFAULT_PERIOD_TICKS > MAX_PERIOD_TICKS) begin
            $error("DEFAULT_PERIOD_TICKS is outside the supported range");
        end
        if (FIFO_DEPTH < 2 || (FIFO_DEPTH & (FIFO_DEPTH - 1)) != 0) begin
            $error("FIFO_DEPTH must be a power of two and at least two");
        end
    end

    always_comb begin
        if (cfg_period_ticks < 32'd2) begin
            sanitized_period = {{(PERIOD_WIDTH-2){1'b0}}, 2'b10};
        end else if (cfg_period_ticks > MAX_PERIOD_TICKS) begin
            sanitized_period = MAX_PERIOD_TICKS[PERIOD_WIDTH-1:0];
        end else begin
            sanitized_period = cfg_period_ticks[PERIOD_WIDTH-1:0];
        end

        // The boundary decision is registered one tick early. This leaves the
        // period counter's normal path as a plain decrement at 200 MHz.
        period_wrap = cfg_enable && period_boundary_pending;

        // A full FIFO applies backpressure for one cycle even if a prefetch is
        // occurring. This avoids device-specific RAM read/write corner cases.
        s_axis_tready = aresetn && cfg_enable && (fifo_count < FIFO_DEPTH);
        stream_push = s_axis_tvalid && s_axis_tready;

        pipeline_busy = scale_valid_1 || scale_valid_2 || scale_valid_3 ||
            duty_pending_valid;
        fifo_prefetch = cfg_enable && (fifo_count != 0) && !pipeline_busy;
        fifo_store = stream_push;

        pwm_out = cfg_enable
            ? (cfg_invert ? !pwm_raw_registered : pwm_raw_registered)
            : 1'b0;

        status_period_ticks = {{(32-PERIOD_WIDTH){1'b0}}, period_active};
        status_duty_ticks = {{(32-PERIOD_WIDTH){1'b0}}, duty_active};
        status_fifo_level = {{(32-COUNT_WIDTH){1'b0}}, fifo_count};
        status_sample_count = {sample_count_high, sample_count_low};
    end

    always_ff @(posedge aclk) begin
        if (!aresetn) begin
            write_pointer <= '0;
            read_pointer <= '0;
            fifo_count <= '0;
            period_active <= DEFAULT_PERIOD_TICKS;
            period_ticks_remaining <= DEFAULT_PERIOD_TICKS - 1;
            period_boundary_pending <= 1'b0;
            duty_active <= '0;
            high_ticks_remaining <= '0;
            pwm_raw_registered <= 1'b0;
            dither_remainder <= 16'd0;
            pwm_period_start <= 1'b0;
            status_underrun_count <= 32'd0;
            sample_count_low <= 32'd0;
            sample_count_high <= 32'd0;

            scale_valid_1 <= 1'b0;
            scale_valid_2 <= 1'b0;
            scale_valid_3 <= 1'b0;
            duty_pending_valid <= 1'b0;
        end else begin
            pwm_period_start <= 1'b0;

            if (clear_status) begin
                status_underrun_count <= 32'd0;
                sample_count_low <= 32'd0;
                sample_count_high <= 32'd0;
            end

            if (!cfg_enable) begin
                // Disabling is a safe stream flush so stale samples cannot be
                // emitted when the output is re-enabled.
                write_pointer <= '0;
                read_pointer <= '0;
                fifo_count <= '0;
                period_active <= sanitized_period;
                period_ticks_remaining <= sanitized_period - 1'b1;
                period_boundary_pending <= 1'b0;
                duty_active <= '0;
                high_ticks_remaining <= '0;
                pwm_raw_registered <= 1'b0;
                dither_remainder <= 16'd0;
                scale_valid_1 <= 1'b0;
                scale_valid_2 <= 1'b0;
                scale_valid_3 <= 1'b0;
                duty_pending_valid <= 1'b0;
            end else begin
                // FIFO writes and scaler prefetches may occur together.
                if (fifo_store) begin
                    fifo_memory[write_pointer] <= s_axis_tdata;
                    write_pointer <= write_pointer + 1'b1;
                end
                if (fifo_prefetch) begin
                    read_pointer <= read_pointer + 1'b1;
                end
                case ({fifo_store, fifo_prefetch})
                    2'b10: fifo_count <= fifo_count + 1'b1;
                    2'b01: fifo_count <= fifo_count - 1'b1;
                    default: fifo_count <= fifo_count;
                endcase

                // Stage 1: snapshot the next queued sample. Dither uses the
                // previous committed remainder; rounding uses half an LSB.
                scale_valid_1 <= fifo_prefetch;
                if (fifo_prefetch) begin
                    scale_level_1 <= fifo_memory[read_pointer] ^ 16'h8000;
                    scale_period_1 <= sanitized_period;
                    scale_addend_1 <= cfg_dither_enable
                        ? dither_remainder
                        : 16'h8000;
                    scale_dither_1 <= cfg_dither_enable;
                    scale_minimum_1 <= ((fifo_memory[read_pointer] ^ 16'h8000) == 16'h0000);
                    scale_maximum_1 <= ((fifo_memory[read_pointer] ^ 16'h8000) == 16'hffff);
                end

                // Stage 2: registered DSP product.
                scale_valid_2 <= scale_valid_1;
                if (scale_valid_1) begin
                    scale_product_2 <= scale_level_1 * scale_period_1;
                    scale_period_2 <= scale_period_1;
                    scale_addend_2 <= scale_addend_1;
                    scale_dither_2 <= scale_dither_1;
                    scale_minimum_2 <= scale_minimum_1;
                    scale_maximum_2 <= scale_maximum_1;
                end

                // Stage 3: error feedback for dithering or nearest rounding.
                scale_valid_3 <= scale_valid_2;
                if (scale_valid_2) begin
                    scale_total_3 <= scale_product_2 + scale_addend_2;
                    scale_period_3 <= scale_period_2;
                    scale_dither_3 <= scale_dither_2;
                    scale_minimum_3 <= scale_minimum_2;
                    scale_maximum_3 <= scale_maximum_2;
                end

                // Completed scale operation becomes the next boundary value.
                if (scale_valid_3) begin
                    duty_pending_valid <= 1'b1;
                    period_pending <= scale_period_3;
                    if (scale_minimum_3) begin
                        duty_pending <= '0;
                        remainder_pending <= 16'd0;
                    end else if (scale_maximum_3) begin
                        duty_pending <= scale_period_3;
                        remainder_pending <= 16'd0;
                    end else begin
                        duty_pending <= (scale_total_3[31:16] > scale_period_3)
                            ? scale_period_3
                            : scale_total_3[31:16];
                        remainder_pending <= scale_dither_3
                            ? scale_total_3[15:0]
                            : 16'd0;
                    end
                end

                if (period_wrap) begin
                    pwm_period_start <= 1'b1;
                    period_boundary_pending <= 1'b0;

                    if (duty_pending_valid) begin
                        duty_active <= duty_pending;
                        period_active <= period_pending;
                        period_ticks_remaining <= period_pending - 1'b1;
                        dither_remainder <= remainder_pending;
                        high_ticks_remaining <= duty_pending;
                        pwm_raw_registered <= (duty_pending != 0);
                        duty_pending_valid <= 1'b0;
                        if (!clear_status) begin
                            if (sample_count_low == 32'hffff_ffff) begin
                                sample_count_low <= 32'd0;
                                sample_count_high <= sample_count_high + 1'b1;
                            end else begin
                                sample_count_low <= sample_count_low + 1'b1;
                            end
                        end
                    end else begin
                        // No new sample: retain the last duty and period.
                        period_ticks_remaining <= period_active - 1'b1;
                        high_ticks_remaining <= duty_active;
                        pwm_raw_registered <= (duty_active != 0);
                        if (!clear_status) begin
                            status_underrun_count <= status_underrun_count + 1'b1;
                        end
                    end
                end else begin
                    period_ticks_remaining <= period_ticks_remaining - 1'b1;
                    if (period_ticks_remaining == {{(PERIOD_WIDTH-1){1'b0}}, 1'b1}) begin
                        period_boundary_pending <= 1'b1;
                    end
                    if (high_ticks_remaining > 1) begin
                        high_ticks_remaining <= high_ticks_remaining - 1'b1;
                        pwm_raw_registered <= 1'b1;
                    end else begin
                        high_ticks_remaining <= '0;
                        pwm_raw_registered <= 1'b0;
                    end
                end
            end
        end
    end

    // TLAST marks DMA buffer boundaries but does not alter continuous timing.
    logic unused_tlast;
    assign unused_tlast = s_axis_tlast;

endmodule
