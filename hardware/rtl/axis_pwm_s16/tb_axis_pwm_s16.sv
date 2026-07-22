`timescale 1ns/1ps

module tb_axis_pwm_s16;
    localparam integer CLOCK_PERIOD_NS = 5;
    localparam integer TEST_PERIOD_TICKS = 8;
    localparam integer TEST_FIFO_DEPTH = 4;

    logic clk = 1'b0;
    logic resetn = 1'b0;
    logic [15:0] axis_tdata = 16'd0;
    logic axis_tvalid = 1'b0;
    logic axis_tready;
    logic axis_tlast = 1'b0;
    logic cfg_enable = 1'b0;
    logic cfg_invert = 1'b0;
    logic cfg_dither_enable = 1'b0;
    logic [31:0] cfg_period_ticks = TEST_PERIOD_TICKS;
    logic clear_status = 1'b0;
    logic pwm_out;
    logic pwm_period_start;
    logic [31:0] status_period_ticks;
    logic [31:0] status_duty_ticks;
    logic [31:0] status_fifo_level;
    logic [31:0] status_underrun_count;
    logic [63:0] status_sample_count;

    integer failures = 0;

    always #(CLOCK_PERIOD_NS / 2.0) clk = !clk;

    axis_pwm_s16 #(
        .DEFAULT_PERIOD_TICKS(TEST_PERIOD_TICKS),
        .FIFO_DEPTH(TEST_FIFO_DEPTH)
    ) dut (
        .aclk(clk),
        .aresetn(resetn),
        .s_axis_tdata(axis_tdata),
        .s_axis_tvalid(axis_tvalid),
        .s_axis_tready(axis_tready),
        .s_axis_tlast(axis_tlast),
        .cfg_enable(cfg_enable),
        .cfg_invert(cfg_invert),
        .cfg_dither_enable(cfg_dither_enable),
        .cfg_period_ticks(cfg_period_ticks),
        .clear_status(clear_status),
        .pwm_out(pwm_out),
        .pwm_period_start(pwm_period_start),
        .status_period_ticks(status_period_ticks),
        .status_duty_ticks(status_duty_ticks),
        .status_fifo_level(status_fifo_level),
        .status_underrun_count(status_underrun_count),
        .status_sample_count(status_sample_count)
    );

    task automatic apply_reset;
        begin
            @(negedge clk);
            resetn = 1'b0;
            cfg_enable = 1'b0;
            axis_tvalid = 1'b0;
            axis_tlast = 1'b0;
            clear_status = 1'b0;
            repeat (4) @(negedge clk);
            resetn = 1'b1;
            // Give the disabled core one clock to adopt cfg_period_ticks and
            // flush any previous pipeline state before enabling it.
            repeat (2) @(negedge clk);
            cfg_enable = 1'b1;
        end
    endtask

    task automatic send_sample(input logic signed [15:0] sample);
        begin
            @(negedge clk);
            axis_tdata = sample;
            axis_tvalid = 1'b1;
            while (!axis_tready) begin
                @(negedge clk);
            end
            @(negedge clk);
            axis_tvalid = 1'b0;
        end
    endtask

    task automatic wait_period_start;
        begin
            @(posedge clk);
            while (!pwm_period_start) begin
                @(posedge clk);
            end
        end
    endtask

    task automatic check_period(
        input integer expected_high_ticks,
        input integer expected_duty_ticks,
        input string label_text
    );
        integer high_ticks;
        integer cycle_index;
        integer ticks_this_period;
        begin
            wait_period_start();
            #1;
            ticks_this_period = status_period_ticks;
            if (status_duty_ticks !== expected_duty_ticks) begin
                $error("%s duty: expected %0d, got %0d", label_text,
                    expected_duty_ticks, status_duty_ticks);
                failures = failures + 1;
            end
            high_ticks = 0;
            for (cycle_index = 0; cycle_index < ticks_this_period; cycle_index++) begin
                @(negedge clk);
                if (pwm_out) begin
                    high_ticks = high_ticks + 1;
                end
            end
            if (high_ticks != expected_high_ticks) begin
                $error("%s waveform: expected %0d high ticks, got %0d",
                    label_text, expected_high_ticks, high_ticks);
                failures = failures + 1;
            end
        end
    endtask

    task automatic expect_equal(
        input logic [63:0] actual,
        input logic [63:0] expected,
        input string label_text
    );
        begin
            if (actual !== expected) begin
                $error("%s: expected %0d, got %0d", label_text, expected, actual);
                failures = failures + 1;
            end
        end
    endtask

    initial begin
        $display("Starting axis_pwm_s16 self-check");

        // Endpoint and midpoint mapping at an eight-tick period.
        cfg_period_ticks = TEST_PERIOD_TICKS;
        cfg_dither_enable = 1'b0;
        apply_reset();
        send_sample(-16'sd32768);
        check_period(0, 0, "minimum sample");

        send_sample(16'sd0);
        check_period(4, 4, "zero sample");

        send_sample(16'sd32767);
        check_period(8, 8, "maximum sample");

        // A period update is boundary-safe and applies to the sample starting
        // the new period. Zero must therefore map to five of ten ticks.
        cfg_period_ticks = 32'd10;
        send_sample(16'sd0);
        check_period(5, 5, "runtime period update");
        expect_equal(status_period_ticks, 10, "active period");

        // Fractional dithering: -16384 maps to 25%. At ten ticks this must
        // alternate 2/3 ticks so four periods total exactly ten high ticks.
        cfg_period_ticks = 32'd10;
        cfg_dither_enable = 1'b1;
        apply_reset();
        send_sample(-16'sd16384);
        send_sample(-16'sd16384);
        send_sample(-16'sd16384);
        send_sample(-16'sd16384);
        check_period(2, 2, "dither period 1");
        check_period(3, 3, "dither period 2");
        check_period(2, 2, "dither period 3");
        check_period(3, 3, "dither period 4");

        // With no queued sample, each boundary holds the last duty and records
        // an underrun rather than producing a malformed pulse.
        wait_period_start();
        wait_period_start();
        #1;
        expect_equal(status_underrun_count, 2, "underrun count");

        // Fill the synchronous FIFO faster than it drains and verify that
        // AXIS backpressure appears at the configured depth.
        cfg_period_ticks = 32'd32;
        cfg_dither_enable = 1'b0;
        apply_reset();
        @(negedge clk);
        axis_tvalid = 1'b1;
        axis_tdata = 16'h0000;
        // One sample may already be in the scaler/pending slot, so fill that
        // slot plus every entry in the FIFO.
        repeat (TEST_FIFO_DEPTH + 1) begin
            while (!axis_tready) @(negedge clk);
            @(negedge clk);
        end
        #1;
        if (axis_tready !== 1'b0) begin
            $error("FIFO full did not deassert TREADY");
            failures = failures + 1;
        end
        axis_tvalid = 1'b0;

        if (failures == 0) begin
            $display("axis_pwm_s16 self-check: PASS");
        end else begin
            $fatal(1, "axis_pwm_s16 self-check: FAIL (%0d failures)", failures);
        end
        $finish;
    end

endmodule
