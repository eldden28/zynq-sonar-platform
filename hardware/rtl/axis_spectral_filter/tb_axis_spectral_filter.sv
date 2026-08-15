`timescale 1ns/1ps

module tb_axis_spectral_filter;
    logic clk = 1'b0;
    logic resetn = 1'b0;
    logic [31:0] input_data;
    logic input_valid;
    logic input_ready;
    logic input_last;
    logic [31:0] output_data;
    logic output_valid;
    logic output_ready = 1'b1;
    logic output_last;
    logic [8:0] low_bin = 9'd8;
    logic [8:0] high_bin = 9'd24;
    logic [8:0] status_bin;
    logic [63:0] frames;
    logic [63:0] samples;
    logic [63:0] filtered;
    integer bin;

    always #5 clk = ~clk;

    axis_spectral_filter dut (
        .aclk(clk),
        .aresetn(resetn),
        .s_axis_tdata(input_data),
        .s_axis_tvalid(input_valid),
        .s_axis_tready(input_ready),
        .s_axis_tlast(input_last),
        .m_axis_tdata(output_data),
        .m_axis_tvalid(output_valid),
        .m_axis_tready(output_ready),
        .m_axis_tlast(output_last),
        .cfg_low_bin(low_bin),
        .cfg_high_bin(high_bin),
        .clear_status(1'b0),
        .status_bin(status_bin),
        .status_frames(frames),
        .status_samples(samples),
        .status_filtered_bins(filtered)
    );

    initial begin
        input_data = 32'd0;
        input_valid = 1'b0;
        input_last = 1'b0;
        repeat (4) @(posedge clk);
        resetn = 1'b1;

        for (bin = 0; bin < 512; bin = bin + 1) begin
            @(negedge clk);
            input_data = 32'h1234_5678 + bin;
            input_valid = 1'b1;
            input_last = (bin == 511);
            @(posedge clk);
            if (!input_ready || !output_valid) begin
                $fatal(1, "stream stalled unexpectedly at bin %0d", bin);
            end
            if (((bin >= 8 && bin <= 24) ||
                 (bin >= 488 && bin <= 504))) begin
                if (output_data !== 32'h1234_5678 + bin) begin
                    $fatal(1, "pass-bin mismatch at %0d", bin);
                end
            end else if (output_data !== 32'd0) begin
                $fatal(1, "stop-bin was not zero at %0d", bin);
            end
            if (output_last !== (bin == 511)) begin
                $fatal(1, "TLAST mismatch at %0d", bin);
            end
        end

        @(negedge clk);
        input_valid = 1'b0;
        input_last = 1'b0;
        @(posedge clk);
        if (frames != 1 || samples != 512 || filtered != 478) begin
            $fatal(1, "counter mismatch frames=%0d samples=%0d filtered=%0d",
                   frames, samples, filtered);
        end
        $display("axis_spectral_filter PASS");
        $finish;
    end
endmodule
