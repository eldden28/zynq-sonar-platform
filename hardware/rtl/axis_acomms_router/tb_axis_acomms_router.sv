`timescale 1ns/1ps

module tb_axis_acomms_router;
    logic clk = 0;
    logic resetn = 0;
    logic [31:0] host_in_data = 0;
    logic host_in_valid = 0;
    logic host_in_ready;
    logic host_in_last = 0;
    logic [31:0] host_out_data;
    logic host_out_valid;
    logic host_out_ready = 1;
    logic host_out_last;
    logic [31:0] accelerator_out_data;
    logic accelerator_out_valid;
    logic accelerator_out_ready;
    logic accelerator_out_last;
    logic [31:0] accelerator_in_data;
    logic accelerator_in_valid;
    logic accelerator_in_ready;
    logic accelerator_in_last;
    logic accelerator_path = 0;
    logic [31:0] divider = 4;
    logic clear_status = 0;
    logic status_path;
    logic status_buffered;
    logic [63:0] status_samples;
    logic [63:0] status_frames;
    integer failures = 0;
    integer cycle = 0;
    integer previous_output_cycle = -100;
    integer received = 0;
    integer expected_base = 0;

    always #5 clk = !clk;
    always @(posedge clk) cycle <= cycle + 1;

    // A stand-in accelerator that adds one while preserving handshakes/TLAST.
    assign accelerator_out_ready = accelerator_in_ready;
    assign accelerator_in_data = accelerator_out_data + 1;
    assign accelerator_in_valid = accelerator_out_valid;
    assign accelerator_in_last = accelerator_out_last;

    axis_acomms_router #(.DEFAULT_DIVIDER(4)) dut (
        .aclk(clk), .aresetn(resetn),
        .s_axis_host_tdata(host_in_data),
        .s_axis_host_tvalid(host_in_valid),
        .s_axis_host_tready(host_in_ready),
        .s_axis_host_tlast(host_in_last),
        .m_axis_host_tdata(host_out_data),
        .m_axis_host_tvalid(host_out_valid),
        .m_axis_host_tready(host_out_ready),
        .m_axis_host_tlast(host_out_last),
        .m_axis_accelerator_tdata(accelerator_out_data),
        .m_axis_accelerator_tvalid(accelerator_out_valid),
        .m_axis_accelerator_tready(accelerator_out_ready),
        .m_axis_accelerator_tlast(accelerator_out_last),
        .s_axis_accelerator_tdata(accelerator_in_data),
        .s_axis_accelerator_tvalid(accelerator_in_valid),
        .s_axis_accelerator_tready(accelerator_in_ready),
        .s_axis_accelerator_tlast(accelerator_in_last),
        .cfg_accelerator_path(accelerator_path),
        .cfg_sample_divider(divider),
        .clear_status(clear_status),
        .status_active_path(status_path),
        .status_buffered(status_buffered),
        .status_samples(status_samples),
        .status_frames(status_frames)
    );

    task automatic reset;
        begin
            resetn = 0;
            host_in_valid = 0;
            repeat (4) @(negedge clk);
            resetn = 1;
            repeat (2) @(negedge clk);
            received = 0;
            previous_output_cycle = -100;
        end
    endtask

    task automatic send(input integer value, input logic last);
        begin
            @(negedge clk);
            host_in_data = value;
            host_in_last = last;
            host_in_valid = 1;
            while (!host_in_ready) @(negedge clk);
            @(negedge clk);
            host_in_valid = 0;
            host_in_last = 0;
        end
    endtask

    always @(posedge clk) begin
        if (resetn && host_out_valid && host_out_ready) begin
            if (host_out_data !== expected_base + received) begin
                $error("sample %0d expected %0d got %0d",
                       received, expected_base + received, host_out_data);
                failures <= failures + 1;
            end
            if (received > 0 && cycle - previous_output_cycle < 4) begin
                $error("pacing interval was %0d cycles", cycle - previous_output_cycle);
                failures <= failures + 1;
            end
            if (host_out_last !== (received == 3)) begin
                $error("TLAST mismatch at sample %0d", received);
                failures <= failures + 1;
            end
            previous_output_cycle <= cycle;
            received <= received + 1;
        end
    end

    initial begin
        reset();
        expected_base = 100;
        fork
            begin
                send(100, 0); send(101, 0); send(102, 0); send(103, 1);
            end
        join_none
        wait (received == 4);
        repeat (2) @(negedge clk);
        if (status_samples != 4 || status_frames != 1 || status_path != 0) begin
            $error("loopback counters/path mismatch");
            failures = failures + 1;
        end

        accelerator_path = 1;
        reset();
        expected_base = 201;
        fork
            begin
                send(200, 0); send(201, 0); send(202, 0); send(203, 1);
            end
        join_none
        wait (received == 4);
        repeat (2) @(negedge clk);
        if (status_samples != 4 || status_frames != 1 || status_path != 1) begin
            $error("accelerator counters/path mismatch");
            failures = failures + 1;
        end

        if (failures == 0)
            $display("axis_acomms_router self-check: PASS");
        else
            $fatal(1, "axis_acomms_router self-check: FAIL (%0d)", failures);
        $finish;
    end
endmodule
