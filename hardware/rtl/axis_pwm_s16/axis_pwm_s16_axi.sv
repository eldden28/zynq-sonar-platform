`timescale 1ns/1ps

// AXI4-Lite register wrapper around the signed-16 AXI4-Stream PWM sink.
module axis_pwm_s16_axi #(
    parameter integer DEFAULT_PERIOD_TICKS = 2000,
    parameter integer FIFO_DEPTH = 64
) (
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME aclk, ASSOCIATED_BUSIF S_AXI:S_AXIS, ASSOCIATED_RESET aresetn, FREQ_HZ 200000000" *)
    (* X_INTERFACE_INFO = "xilinx.com:signal:clock:1.0 aclk CLK" *)
    input  logic        aclk,
    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME aresetn, POLARITY ACTIVE_LOW" *)
    (* X_INTERFACE_INFO = "xilinx.com:signal:reset:1.0 aresetn RST" *)
    input  logic        aresetn,

    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXIS, TDATA_NUM_BYTES 2, HAS_TLAST 1, HAS_TKEEP 0, HAS_TSTRB 0" *)
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TDATA" *)
    input  logic [15:0] s_axis_tdata,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TVALID" *)
    input  logic        s_axis_tvalid,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TREADY" *)
    output logic        s_axis_tready,
    (* X_INTERFACE_INFO = "xilinx.com:interface:axis:1.0 S_AXIS TLAST" *)
    input  logic        s_axis_tlast,

    (* X_INTERFACE_PARAMETER = "XIL_INTERFACENAME S_AXI, PROTOCOL AXI4LITE, DATA_WIDTH 32, ADDR_WIDTH 6, READ_WRITE_MODE READ_WRITE, MAX_BURST_LENGTH 256" *)
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
    input  logic        s_axi_rready,

    output logic        pwm_out,
    output logic        pwm_period_start
);

    localparam logic [31:0] CORE_ID = 32'h5057_4d31; // "PWM1"
    localparam logic [31:0] CORE_VERSION = 32'h0001_0000;

    logic [31:0] control_register;
    logic [31:0] period_register;
    logic        clear_status;

    logic [31:0] status_period_ticks;
    logic [31:0] status_duty_ticks;
    logic [31:0] status_fifo_level;
    logic [31:0] status_underrun_count;
    logic [63:0] status_sample_count;

    logic [5:0]  write_address;
    logic [31:0] write_data;
    logic [3:0]  write_strobes;
    logic        write_address_stored;
    logic        write_data_stored;

    logic unused_awprot;
    logic unused_arprot;
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
                4'h0: read_register = control_register;
                4'h1: read_register = period_register;
                4'h2: read_register = 32'd0;
                4'h3: read_register = {
                    26'd0,
                    (status_fifo_level == FIFO_DEPTH),
                    (status_fifo_level == 0),
                    pwm_period_start,
                    control_register[2:0]
                };
                4'h4: read_register = status_period_ticks;
                4'h5: read_register = status_duty_ticks;
                4'h6: read_register = status_fifo_level;
                4'h7: read_register = status_underrun_count;
                4'h8: read_register = status_sample_count[31:0];
                4'h9: read_register = status_sample_count[63:32];
                4'ha: read_register = CORE_ID;
                4'hb: read_register = CORE_VERSION;
                default: read_register = 32'h0000_0000;
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
            control_register <= 32'h0000_0004;
            period_register <= DEFAULT_PERIOD_TICKS;
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
                    4'h0: control_register <= apply_write_strobes(
                        control_register, write_data, write_strobes);
                    4'h1: period_register <= apply_write_strobes(
                        period_register, write_data, write_strobes);
                    4'h2: clear_status <= write_strobes[0] && write_data[0];
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

    axis_pwm_s16 #(
        .DEFAULT_PERIOD_TICKS(DEFAULT_PERIOD_TICKS),
        .FIFO_DEPTH(FIFO_DEPTH),
        .PERIOD_WIDTH(16)
    ) pwm_core (
        .aclk(aclk),
        .aresetn(aresetn),
        .s_axis_tdata(s_axis_tdata),
        .s_axis_tvalid(s_axis_tvalid),
        .s_axis_tready(s_axis_tready),
        .s_axis_tlast(s_axis_tlast),
        .cfg_enable(control_register[0]),
        .cfg_invert(control_register[1]),
        .cfg_dither_enable(control_register[2]),
        .cfg_period_ticks(period_register),
        .clear_status(clear_status),
        .pwm_out(pwm_out),
        .pwm_period_start(pwm_period_start),
        .status_period_ticks(status_period_ticks),
        .status_duty_ticks(status_duty_ticks),
        .status_fifo_level(status_fifo_level),
        .status_underrun_count(status_underrun_count),
        .status_sample_count(status_sample_count)
    );

endmodule
