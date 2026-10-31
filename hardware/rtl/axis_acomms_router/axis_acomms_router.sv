`timescale 1ns/1ps

// Runtime-selectable AXI4-Stream acoustic processing slot.
//
// In loopback mode, S_AXIS_HOST is paced and returned on M_AXIS_HOST. In
// accelerator mode, host samples leave through M_AXIS_ACCELERATOR and the
// processed samples arriving on S_AXIS_ACCELERATOR are paced back to the host.
// The one-beat output register provides legal AXI backpressure and preserves
// TLAST. A sample-rate divider of N permits at most one output transfer every
// N aclk cycles.
module axis_acomms_router #(
    parameter integer DEFAULT_DIVIDER = 400
) (
    input  logic        aclk,
    input  logic        aresetn,

    input  logic [31:0] s_axis_host_tdata,
    input  logic        s_axis_host_tvalid,
    output logic        s_axis_host_tready,
    input  logic        s_axis_host_tlast,

    output logic [31:0] m_axis_host_tdata,
    output logic        m_axis_host_tvalid,
    input  logic        m_axis_host_tready,
    output logic        m_axis_host_tlast,

    output logic [31:0] m_axis_accelerator_tdata,
    output logic        m_axis_accelerator_tvalid,
    input  logic        m_axis_accelerator_tready,
    output logic        m_axis_accelerator_tlast,

    input  logic [31:0] s_axis_accelerator_tdata,
    input  logic        s_axis_accelerator_tvalid,
    output logic        s_axis_accelerator_tready,
    input  logic        s_axis_accelerator_tlast,

    input  logic        cfg_accelerator_path,
    input  logic [31:0] cfg_sample_divider,
    input  logic        clear_status,

    output logic        status_active_path,
    output logic        status_buffered,
    output logic [63:0] status_samples,
    output logic [63:0] status_frames
);

    logic [31:0] output_data;
    logic        output_last;
    logic        output_valid;
    logic [31:0] pace_counter;
    logic [31:0] effective_divider;
    logic        selected_valid;
    logic [31:0] selected_data;
    logic        selected_last;
    logic        selected_ready;
    logic        output_transfer;

    initial begin
        if (DEFAULT_DIVIDER < 1) begin
            $error("DEFAULT_DIVIDER must be positive");
        end
    end

    always_comb begin
        effective_divider = cfg_sample_divider == 0 ? 32'd1 : cfg_sample_divider;

        // The DMA-to-accelerator leg is intentionally unpaced. The returned
        // samples are paced at the host boundary, propagating backpressure
        // through well-behaved accelerator IP.
        m_axis_accelerator_tdata = s_axis_host_tdata;
        m_axis_accelerator_tlast = s_axis_host_tlast;
        m_axis_accelerator_tvalid =
            cfg_accelerator_path && s_axis_host_tvalid;

        selected_data = cfg_accelerator_path ?
            s_axis_accelerator_tdata : s_axis_host_tdata;
        selected_last = cfg_accelerator_path ?
            s_axis_accelerator_tlast : s_axis_host_tlast;
        selected_valid = cfg_accelerator_path ?
            s_axis_accelerator_tvalid : s_axis_host_tvalid;
        selected_ready = !output_valid;

        s_axis_host_tready = cfg_accelerator_path ?
            m_axis_accelerator_tready : selected_ready;
        s_axis_accelerator_tready =
            cfg_accelerator_path && selected_ready;

        m_axis_host_tdata = output_data;
        m_axis_host_tlast = output_last;
        m_axis_host_tvalid = output_valid && pace_counter == 0;
        output_transfer = m_axis_host_tvalid && m_axis_host_tready;

        status_active_path = cfg_accelerator_path;
        status_buffered = output_valid;
    end

    always_ff @(posedge aclk) begin
        if (!aresetn) begin
            output_data <= 32'd0;
            output_last <= 1'b0;
            output_valid <= 1'b0;
            pace_counter <= 32'd0;
            status_samples <= 64'd0;
            status_frames <= 64'd0;
        end else begin
            if (pace_counter != 0) begin
                pace_counter <= pace_counter - 1'b1;
            end

            if (!output_valid && selected_valid && selected_ready) begin
                output_data <= selected_data;
                output_last <= selected_last;
                output_valid <= 1'b1;
            end

            if (output_transfer) begin
                output_valid <= 1'b0;
                pace_counter <= effective_divider - 1'b1;
                status_samples <= status_samples + 1'b1;
                if (output_last) begin
                    status_frames <= status_frames + 1'b1;
                end
            end

            if (clear_status) begin
                status_samples <= 64'd0;
                status_frames <= 64'd0;
            end
        end
    end

endmodule
