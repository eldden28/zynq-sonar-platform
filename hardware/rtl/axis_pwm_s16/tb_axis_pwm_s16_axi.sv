`timescale 1ns/1ps

module tb_axis_pwm_s16_axi;
    logic clock = 1'b0;
    logic resetn = 1'b0;

    logic [15:0] axis_tdata = 16'd0;
    logic axis_tvalid = 1'b0;
    logic axis_tready;
    logic axis_tlast = 1'b0;

    logic [5:0] awaddr = '0;
    logic [2:0] awprot = '0;
    logic awvalid = 1'b0;
    logic awready;
    logic [31:0] wdata = '0;
    logic [3:0] wstrb = 4'hf;
    logic wvalid = 1'b0;
    logic wready;
    logic [1:0] bresp;
    logic bvalid;
    logic bready = 1'b0;
    logic [5:0] araddr = '0;
    logic [2:0] arprot = '0;
    logic arvalid = 1'b0;
    logic arready;
    logic [31:0] rdata;
    logic [1:0] rresp;
    logic rvalid;
    logic rready = 1'b0;
    logic pwm_out;
    logic pwm_period_start;

    integer failures = 0;

    always #2.5 clock = !clock;

    axis_pwm_s16_axi #(
        .DEFAULT_PERIOD_TICKS(8),
        .FIFO_DEPTH(4)
    ) dut (
        .aclk(clock),
        .aresetn(resetn),
        .s_axis_tdata(axis_tdata),
        .s_axis_tvalid(axis_tvalid),
        .s_axis_tready(axis_tready),
        .s_axis_tlast(axis_tlast),
        .s_axi_awaddr(awaddr),
        .s_axi_awprot(awprot),
        .s_axi_awvalid(awvalid),
        .s_axi_awready(awready),
        .s_axi_wdata(wdata),
        .s_axi_wstrb(wstrb),
        .s_axi_wvalid(wvalid),
        .s_axi_wready(wready),
        .s_axi_bresp(bresp),
        .s_axi_bvalid(bvalid),
        .s_axi_bready(bready),
        .s_axi_araddr(araddr),
        .s_axi_arprot(arprot),
        .s_axi_arvalid(arvalid),
        .s_axi_arready(arready),
        .s_axi_rdata(rdata),
        .s_axi_rresp(rresp),
        .s_axi_rvalid(rvalid),
        .s_axi_rready(rready),
        .pwm_out(pwm_out),
        .pwm_period_start(pwm_period_start)
    );

    task automatic axi_write(
        input logic [5:0] address,
        input logic [31:0] value,
        input logic [3:0] strobes
    );
        begin
            @(negedge clock);
            awaddr = address;
            awvalid = 1'b1;
            wdata = value;
            wstrb = strobes;
            wvalid = 1'b1;
            bready = 1'b1;
            while (!(awready && wready)) begin
                @(negedge clock);
            end
            @(negedge clock);
            awvalid = 1'b0;
            wvalid = 1'b0;
            while (!bvalid) begin
                @(negedge clock);
            end
            if (bresp != 2'b00) begin
                $error("AXI write returned BRESP %b", bresp);
                failures = failures + 1;
            end
            @(negedge clock);
            bready = 1'b0;
        end
    endtask

    task automatic axi_read(
        input logic [5:0] address,
        output logic [31:0] value
    );
        begin
            @(negedge clock);
            araddr = address;
            arvalid = 1'b1;
            rready = 1'b1;
            while (!arready) begin
                @(negedge clock);
            end
            @(negedge clock);
            arvalid = 1'b0;
            while (!rvalid) begin
                @(negedge clock);
            end
            value = rdata;
            if (rresp != 2'b00) begin
                $error("AXI read returned RRESP %b", rresp);
                failures = failures + 1;
            end
            @(negedge clock);
            rready = 1'b0;
        end
    endtask

    task automatic expect_equal(
        input logic [31:0] actual,
        input logic [31:0] expected,
        input string label
    );
        begin
            if (actual !== expected) begin
                $error("%s: expected 0x%08x, got 0x%08x", label, expected, actual);
                failures = failures + 1;
            end
        end
    endtask

    task automatic send_sample(input logic signed [15:0] sample);
        begin
            @(negedge clock);
            axis_tdata = sample;
            axis_tvalid = 1'b1;
            while (!axis_tready) begin
                @(negedge clock);
            end
            @(negedge clock);
            axis_tvalid = 1'b0;
        end
    endtask

    logic [31:0] read_value;
    integer high_ticks;
    integer cycle_index;

    initial begin
        repeat (4) @(posedge clock);
        resetn = 1'b1;
        repeat (2) @(posedge clock);

        axi_read(6'h28, read_value);
        expect_equal(read_value, 32'h5057_4d31, "core ID");
        axi_read(6'h2c, read_value);
        expect_equal(read_value, 32'h0001_0000, "core version");
        axi_read(6'h00, read_value);
        expect_equal(read_value, 32'h0000_0004, "reset control");
        axi_read(6'h04, read_value);
        expect_equal(read_value, 32'd8, "reset period");

        // Byte strobes must preserve untouched control bytes.
        axi_write(6'h00, 32'hffff_ff05, 4'b0001);
        axi_read(6'h00, read_value);
        expect_equal(read_value, 32'h0000_0005, "enable and dither control");

        axi_write(6'h04, 32'd10, 4'hf);
        axi_read(6'h04, read_value);
        expect_equal(read_value, 32'd10, "runtime period register");

        send_sample(16'sd0);
        // The sample may arrive too close to the current boundary to clear the
        // scaler pipeline. Wait for the boundary where it is actually active.
        while (!(pwm_period_start && dut.status_duty_ticks == 32'd5)) begin
            @(posedge clock);
            #1;
        end
        high_ticks = 0;
        for (cycle_index = 0; cycle_index < 10; cycle_index = cycle_index + 1) begin
            @(negedge clock);
            if (pwm_out) begin
                high_ticks = high_ticks + 1;
            end
        end
        if (high_ticks != 5) begin
            $error("zero sample: expected 5 high ticks, got %0d", high_ticks);
            failures = failures + 1;
        end

        axi_read(6'h10, read_value);
        expect_equal(read_value, 32'd10, "active period status");
        axi_read(6'h14, read_value);
        expect_equal(read_value, 32'd5, "active duty status");

        if (failures == 0) begin
            $display("axis_pwm_s16_axi self-check: PASS");
        end else begin
            $fatal(1, "axis_pwm_s16_axi self-check: %0d failure(s)", failures);
        end
        $finish;
    end

    initial begin
        #5000;
        $fatal(1, "axis_pwm_s16_axi self-check timed out");
    end

endmodule
