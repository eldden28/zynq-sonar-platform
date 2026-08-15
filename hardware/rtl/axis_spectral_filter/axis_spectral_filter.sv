`timescale 1ns/1ps

// Symmetric 512-bin frequency-domain mask for real-valued signals.
//
// Xilinx FFT data is packed as signed Q1.15 {imaginary, real}. Bins in the
// inclusive positive-frequency range cfg_low_bin..cfg_high_bin and their
// negative-frequency mirrors pass unchanged. All other complex bins become
// zero. TLAST defines the frame boundary and is preserved.
module axis_spectral_filter #(
    parameter integer FFT_LENGTH = 512
) (
    input  logic        aclk,
    input  logic        aresetn,

    input  logic [31:0] s_axis_tdata,
    input  logic        s_axis_tvalid,
    output logic        s_axis_tready,
    input  logic        s_axis_tlast,

    output logic [31:0] m_axis_tdata,
    output logic        m_axis_tvalid,
    input  logic        m_axis_tready,
    output logic        m_axis_tlast,

    input  logic [8:0]  cfg_low_bin,
    input  logic [8:0]  cfg_high_bin,
    input  logic        clear_status,

    output logic [8:0]  status_bin,
    output logic [63:0] status_frames,
    output logic [63:0] status_samples,
    output logic [63:0] status_filtered_bins
);

    localparam integer BIN_WIDTH = $clog2(FFT_LENGTH);
    localparam integer NYQUIST_BIN = FFT_LENGTH / 2;

    logic [BIN_WIDTH-1:0] bin_index;
    logic                 stream_transfer;
    logic                 positive_pass;
    logic                 negative_pass;
    logic                 pass_bin;

    initial begin
        if (FFT_LENGTH != 512) begin
            $error("axis_spectral_filter currently requires FFT_LENGTH=512");
        end
    end

    always_comb begin
        s_axis_tready = m_axis_tready;
        m_axis_tvalid = s_axis_tvalid;
        m_axis_tlast = s_axis_tlast;
        stream_transfer = s_axis_tvalid && s_axis_tready;

        positive_pass =
            (bin_index >= cfg_low_bin) && (bin_index <= cfg_high_bin);
        negative_pass =
            (cfg_low_bin != 0) &&
            (bin_index >= (FFT_LENGTH - cfg_high_bin)) &&
            (bin_index <= (FFT_LENGTH - cfg_low_bin));

        // For a low-pass beginning at DC, the negative mirror runs through
        // bin 511 because bin 512 aliases bin zero.
        if (cfg_low_bin == 0) begin
            negative_pass = bin_index >= (FFT_LENGTH - cfg_high_bin);
        end

        pass_bin = positive_pass || negative_pass;
        m_axis_tdata = pass_bin ? s_axis_tdata : 32'd0;
        status_bin = bin_index;
    end

    always_ff @(posedge aclk) begin
        if (!aresetn) begin
            bin_index <= '0;
            status_frames <= 64'd0;
            status_samples <= 64'd0;
            status_filtered_bins <= 64'd0;
        end else begin
            if (clear_status) begin
                status_frames <= 64'd0;
                status_samples <= 64'd0;
                status_filtered_bins <= 64'd0;
            end

            if (stream_transfer) begin
                status_samples <= status_samples + 1'b1;
                if (!pass_bin) begin
                    status_filtered_bins <= status_filtered_bins + 1'b1;
                end

                if (s_axis_tlast || bin_index == FFT_LENGTH - 1) begin
                    bin_index <= '0;
                    status_frames <= status_frames + 1'b1;
                end else begin
                    bin_index <= bin_index + 1'b1;
                end
            end
        end
    end

endmodule
