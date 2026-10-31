`timescale 1ns/1ps

// AXI4-Lite control wrapper for the acoustic AXI-stream router.
module axis_acomms_router_axi #(
    parameter integer CLOCK_HZ = 100000000,
    parameter integer DEFAULT_SAMPLE_RATE = 250000
) (
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME aclk, ASSOCIATED_BUSIF S_AXI:S_AXIS_HOST:M_AXIS_HOST:M_AXIS_ACCELERATOR:S_AXIS_ACCELERATOR, ASSOCIATED_RESET aresetn" *)
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK" *)
    input  logic        aclk,
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME aresetn, POLARITY ACTIVE_LOW" *)
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST" *)
    input  logic        aresetn,

    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXIS_HOST, TDATA_NUM_BYTES 4, HAS_TLAST 1, HAS_TKEEP 0, HAS_TSTRB 0" *)
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS_HOST TDATA" *)
    input  logic [31:0] s_axis_host_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS_HOST TVALID" *)
    input  logic        s_axis_host_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS_HOST TREADY" *)
    output logic        s_axis_host_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS_HOST TLAST" *)
    input  logic        s_axis_host_tlast,

    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME M_AXIS_HOST, TDATA_NUM_BYTES 4, HAS_TLAST 1, HAS_TKEEP 0, HAS_TSTRB 0" *)
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS_HOST TDATA" *)
    output logic [31:0] m_axis_host_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS_HOST TVALID" *)
    output logic        m_axis_host_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS_HOST TREADY" *)
    input  logic        m_axis_host_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS_HOST TLAST" *)
    output logic        m_axis_host_tlast,

    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME M_AXIS_ACCELERATOR, TDATA_NUM_BYTES 4, HAS_TLAST 1, HAS_TKEEP 0, HAS_TSTRB 0" *)
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS_ACCELERATOR TDATA" *)
    output logic [31:0] m_axis_accelerator_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS_ACCELERATOR TVALID" *)
    output logic        m_axis_accelerator_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS_ACCELERATOR TREADY" *)
    input  logic        m_axis_accelerator_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 M_AXIS_ACCELERATOR TLAST" *)
    output logic        m_axis_accelerator_tlast,

    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXIS_ACCELERATOR, TDATA_NUM_BYTES 4, HAS_TLAST 1, HAS_TKEEP 0, HAS_TSTRB 0" *)
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS_ACCELERATOR TDATA" *)
    input  logic [31:0] s_axis_accelerator_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS_ACCELERATOR TVALID" *)
    input  logic        s_axis_accelerator_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS_ACCELERATOR TREADY" *)
    output logic        s_axis_accelerator_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS_ACCELERATOR TLAST" *)
    input  logic        s_axis_accelerator_tlast,

    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXI, PROTOCOL AXI4LITE, DATA_WIDTH 32, ADDR_WIDTH 6, READ_WRITE_MODE READ_WRITE" *)
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWADDR" *)
    input  logic [5:0]  s_axi_awaddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWPROT" *)
    input  logic [2:0]  s_axi_awprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWVALID" *)
    input  logic        s_axi_awvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI AWREADY" *)
    output logic        s_axi_awready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WDATA" *)
    input  logic [31:0] s_axi_wdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WSTRB" *)
    input  logic [3:0]  s_axi_wstrb,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WVALID" *)
    input  logic        s_axi_wvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI WREADY" *)
    output logic        s_axi_wready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BRESP" *)
    output logic [1:0]  s_axi_bresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BVALID" *)
    output logic        s_axi_bvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI BREADY" *)
    input  logic        s_axi_bready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARADDR" *)
    input  logic [5:0]  s_axi_araddr,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARPROT" *)
    input  logic [2:0]  s_axi_arprot,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARVALID" *)
    input  logic        s_axi_arvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI ARREADY" *)
    output logic        s_axi_arready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RDATA" *)
    output logic [31:0] s_axi_rdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RRESP" *)
    output logic [1:0]  s_axi_rresp,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RVALID" *)
    output logic        s_axi_rvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:aximm:1.0 S_AXI RREADY" *)
    input  logic        s_axi_rready
);

    localparam logic [31:0] CORE_ID = 32'h4143_4f4d; // "ACOM"
    localparam logic [31:0] CORE_VERSION = 32'h0001_0000;
    localparam integer DEFAULT_DIVIDER = CLOCK_HZ / DEFAULT_SAMPLE_RATE;

    logic        accelerator_path_register;
    logic [31:0] sample_divider_register;
    logic [31:0] sample_rate_register;
    logic        clear_status;
    logic        status_active_path;
    logic        status_buffered;
    logic [63:0] status_samples;
    logic [63:0] status_frames;
    logic [5:0]  write_address;
    logic [31:0] write_data;
    logic [3:0]  write_strobes;
    logic        write_address_stored;
    logic        write_data_stored;
    logic        unused_awprot;
    logic        unused_arprot;

    initial begin
        if (CLOCK_HZ < 1 || DEFAULT_SAMPLE_RATE < 1 ||
            CLOCK_HZ % DEFAULT_SAMPLE_RATE != 0) begin
            $error("CLOCK_HZ must be an integer multiple of DEFAULT_SAMPLE_RATE");
        end
    end

    assign unused_awprot = ^s_axi_awprot;
    assign unused_arprot = ^s_axi_arprot;

    function automatic [31:0] apply_write_strobes(
        input logic [31:0] previous_value,
        input logic [31:0] new_value,
        input logic [3:0]  strobes
    );
        integer byte_index;
        begin
            apply_write_strobes = previous_value;
            for (byte_index = 0; byte_index < 4; byte_index = byte_index + 1) begin
                if (strobes[byte_index]) begin
                    apply_write_strobes[byte_index*8 +: 8] =
                        new_value[byte_index*8 +: 8];
                end
            end
        end
    endfunction

    function automatic [31:0] read_register(input logic [5:0] address);
        begin
            case (address[5:2])
                4'h0: read_register = {31'd0, accelerator_path_register};
                4'h1: read_register = sample_divider_register;
                4'h2: read_register = sample_rate_register;
                4'h3: read_register = {30'd0, status_buffered, status_active_path};
                4'h4: read_register = status_samples[31:0];
                4'h5: read_register = status_samples[63:32];
                4'h6: read_register = status_frames[31:0];
                4'h7: read_register = status_frames[63:32];
                4'h8: read_register = CORE_ID;
                4'h9: read_register = CORE_VERSION;
                4'ha: read_register = CLOCK_HZ;
                4'hb: read_register = DEFAULT_SAMPLE_RATE;
                default: read_register = 32'd0;
            endcase
        end
    endfunction

    assign s_axi_awready = aresetn && !write_address_stored && !s_axi_bvalid;
    assign s_axi_wready = aresetn && !write_data_stored && !s_axi_bvalid;
    assign s_axi_bresp = 2'b00;
    assign s_axi_arready = aresetn && !s_axi_rvalid;
    assign s_axi_rresp = 2'b00;

    always_ff @(posedge aclk) begin
        if (!aresetn) begin
            accelerator_path_register <= 1'b0;
            sample_divider_register <= DEFAULT_DIVIDER;
            sample_rate_register <= DEFAULT_SAMPLE_RATE;
            clear_status <= 1'b0;
            write_address <= '0;
            write_data <= '0;
            write_strobes <= '0;
            write_address_stored <= 1'b0;
            write_data_stored <= 1'b0;
            s_axi_bvalid <= 1'b0;
            s_axi_rdata <= 32'd0;
            s_axi_rvalid <= 1'b0;
        end else begin
            clear_status <= 1'b0;
            if (s_axi_awvalid && s_axi_awready) begin
                write_address <= s_axi_awaddr;
                write_address_stored <= 1'b1;
            end
            if (s_axi_wvalid && s_axi_wready) begin
                write_data <= s_axi_wdata;
                write_strobes <= s_axi_wstrb;
                write_data_stored <= 1'b1;
            end
            if (!s_axi_bvalid && write_address_stored && write_data_stored) begin
                case (write_address[5:2])
                    4'h0: begin
                        if (write_strobes[0]) begin
                            accelerator_path_register <= write_data[0];
                            clear_status <= write_data[1];
                        end
                    end
                    4'h1: begin
                        if (apply_write_strobes(sample_divider_register,
                                write_data, write_strobes) != 0) begin
                            sample_divider_register <= apply_write_strobes(
                                sample_divider_register, write_data, write_strobes);
                        end
                    end
                    4'h2: begin
                        if (apply_write_strobes(sample_rate_register,
                                write_data, write_strobes) != 0) begin
                            sample_rate_register <= apply_write_strobes(
                                sample_rate_register, write_data, write_strobes);
                        end
                    end
                    default: begin end
                endcase
                write_address_stored <= 1'b0;
                write_data_stored <= 1'b0;
                s_axi_bvalid <= 1'b1;
            end else if (s_axi_bvalid && s_axi_bready) begin
                s_axi_bvalid <= 1'b0;
            end
            if (s_axi_arvalid && s_axi_arready) begin
                s_axi_rdata <= read_register(s_axi_araddr);
                s_axi_rvalid <= 1'b1;
            end else if (s_axi_rvalid && s_axi_rready) begin
                s_axi_rvalid <= 1'b0;
            end
        end
    end

    axis_acomms_router #(
        .DEFAULT_DIVIDER(DEFAULT_DIVIDER)
    ) router_core (
        .aclk(aclk),
        .aresetn(aresetn),
        .s_axis_host_tdata(s_axis_host_tdata),
        .s_axis_host_tvalid(s_axis_host_tvalid),
        .s_axis_host_tready(s_axis_host_tready),
        .s_axis_host_tlast(s_axis_host_tlast),
        .m_axis_host_tdata(m_axis_host_tdata),
        .m_axis_host_tvalid(m_axis_host_tvalid),
        .m_axis_host_tready(m_axis_host_tready),
        .m_axis_host_tlast(m_axis_host_tlast),
        .m_axis_accelerator_tdata(m_axis_accelerator_tdata),
        .m_axis_accelerator_tvalid(m_axis_accelerator_tvalid),
        .m_axis_accelerator_tready(m_axis_accelerator_tready),
        .m_axis_accelerator_tlast(m_axis_accelerator_tlast),
        .s_axis_accelerator_tdata(s_axis_accelerator_tdata),
        .s_axis_accelerator_tvalid(s_axis_accelerator_tvalid),
        .s_axis_accelerator_tready(s_axis_accelerator_tready),
        .s_axis_accelerator_tlast(s_axis_accelerator_tlast),
        .cfg_accelerator_path(accelerator_path_register),
        .cfg_sample_divider(sample_divider_register),
        .clear_status(clear_status),
        .status_active_path(status_active_path),
        .status_buffered(status_buffered),
        .status_samples(status_samples),
        .status_frames(status_frames)
    );

endmodule
