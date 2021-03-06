# Copyright (c) 2020, Xilinx
# All rights reserved.
#
# Redistribution and use in source and binary forms, with or without
# modification, are permitted provided that the following conditions are met:
#
# * Redistributions of source code must retain the above copyright notice, this
#   list of conditions and the following disclaimer.
#
# * Redistributions in binary form must reproduce the above copyright notice,
#   this list of conditions and the following disclaimer in the documentation
#   and/or other materials provided with the distribution.
#
# * Neither the name of FINN nor the names of its
#   contributors may be used to endorse or promote products derived from
#   this software without specific prior written permission.
#
# THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS"
# AND ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
# IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE
# DISCLAIMED. IN NO EVENT SHALL THE COPYRIGHT HOLDER OR CONTRIBUTORS BE LIABLE
# FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
# DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR
# SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER
# CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY,
# OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE
# OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF SUCH DAMAGE.

from abc import abstractmethod
import numpy as np
import os
import subprocess
from finn.custom_op import CustomOp
from finn.util.basic import CppBuilder
from finn.util.fpgadataflow import (
    IPGenBuilder,
    pyverilate_get_liveness_threshold_cycles,
)
from . import templates


class HLSCustomOp(CustomOp):
    """HLSCustomOp class all custom ops that correspond to a finn-hlslib
    function are based on. Contains different functions every fpgadataflow
    custom node should have. Some as abstract methods, these have to be filled
    when writing a new fpgadataflow custom op node."""

    def __init__(self, onnx_node):
        super().__init__(onnx_node)

        self.code_gen_dict = {}

        # getting templates from templates.py

        # template for single node execution
        self.docompute_template = templates.docompute_template

        # templates for single node ip generation
        # cpp file
        self.ipgen_template = templates.ipgen_template
        # tcl script
        self.ipgentcl_template = templates.ipgentcl_template

    def get_nodeattr_types(self):
        return {
            "backend": ("s", True, "fpgadataflow"),
            "code_gen_dir_npysim": ("s", False, ""),
            "code_gen_dir_ipgen": ("s", False, ""),
            "executable_path": ("s", False, ""),
            "ipgen_path": ("s", False, ""),
            "exec_mode": ("s", False, ""),
            "sim_cycles": ("i", False, 0),
            "rtlsim_trace": ("s", False, ""),
        }

    def node_res_estimation(self):
        """Returns summarized resource estimation of BRAMs and LUTs
        of the node."""
        resources = []
        resources.append("BRAMs: " + str(self.bram_estimation()))
        resources.append("LUTs: " + str(self.lut_estimation()))
        return resources

    def bram_estimation(self):
        """Function for BRAM resource estimation, is member function of
        HLSCustomOp class but has to be filled by every node"""
        return 0

    def lut_estimation(self):
        """Function for LUT resource estimation, is member function of
        HLSCustomOp class but has to be filled by every node"""
        return 0

    def code_generation_ipgen(self, model, fpgapart, clk):
        """Generates c++ code and tcl script for ip generation."""
        node = self.onnx_node

        # generate top cpp file for ip generation
        path = self.get_nodeattr("code_gen_dir_ipgen")
        self.generate_params(model, path)
        self.global_includes()
        self.defines("ipgen")
        self.blackboxfunction()
        self.pragmas()
        self.docompute()

        template = self.ipgen_template

        for key in self.code_gen_dict:
            # transform list into long string separated by '\n'
            code_gen_line = "\n".join(self.code_gen_dict[key])
            template = template.replace(key, code_gen_line)
        code_gen_dir = self.get_nodeattr("code_gen_dir_ipgen")
        f = open(os.path.join(code_gen_dir, "top_{}.cpp".format(node.name)), "w")
        f.write(template)
        f.close()
        self.code_gen_dict.clear()

        # generate tcl script for ip generation
        self.code_gen_dict["$PROJECTNAME$"] = ["project_{}".format(node.name)]
        self.code_gen_dict["$HWSRCDIR$"] = [code_gen_dir]
        self.code_gen_dict["$FPGAPART$"] = [fpgapart]
        self.code_gen_dict["$FINNHLSLIBDIR$"] = ["/workspace/finn-hlslib"]
        self.code_gen_dict["$TOPFXN$"] = [node.name]
        self.code_gen_dict["$CLKPERIOD$"] = [str(clk)]

        template = self.ipgentcl_template

        for key in self.code_gen_dict:
            # transform list into long string separated by '\n'
            code_gen_line = "\n".join(self.code_gen_dict[key])
            template = template.replace(key, code_gen_line)
        code_gen_dir = self.get_nodeattr("code_gen_dir_ipgen")
        f = open(os.path.join(code_gen_dir, "hls_syn_{}.tcl".format(node.name)), "w")
        f.write(template)
        f.close()
        self.code_gen_dict.clear()

    def ipgen_singlenode_code(self):
        """Builds the bash script for ip generation using the IPGenBuilder from
        finn.util.fpgadataflow."""
        node = self.onnx_node
        code_gen_dir = self.get_nodeattr("code_gen_dir_ipgen")
        builder = IPGenBuilder()
        builder.append_tcl(code_gen_dir + "/hls_syn_{}.tcl".format(node.name))
        builder.set_ipgen_path(code_gen_dir + "/project_{}".format(node.name))
        builder.build(code_gen_dir)
        self.set_nodeattr("ipgen_path", builder.ipgen_path)

    def code_generation_npysim(self, model):
        """Generates c++ code for simulation (npysim)."""
        node = self.onnx_node
        path = self.get_nodeattr("code_gen_dir_npysim")
        self.generate_params(model, path)
        self.global_includes()
        self.defines("npysim")
        self.read_npy_data()
        self.strm_decl()
        self.docompute()
        self.dataoutstrm()
        self.save_as_npy()

        template = self.docompute_template

        for key in self.code_gen_dict:
            # transform list into long string separated by '\n'
            code_gen_line = "\n".join(self.code_gen_dict[key])
            template = template.replace(key, code_gen_line)
        code_gen_dir = self.get_nodeattr("code_gen_dir_npysim")
        f = open(os.path.join(code_gen_dir, "execute_{}.cpp".format(node.op_type)), "w")
        f.write(template)
        f.close()
        self.code_gen_dict.clear()

    def compile_singlenode_code(self):
        """Builds the bash script for compilation using the CppBuilder from
        finn.util.basic and executes the script to produce the executable."""
        code_gen_dir = self.get_nodeattr("code_gen_dir_npysim")
        builder = CppBuilder()
        # to enable additional debug features please uncommand the next line
        # builder.append_includes("-DDEBUG")
        builder.append_includes("-I/workspace/finn/src/finn/data/cpp")
        builder.append_includes("-I/workspace/cnpy/")
        builder.append_includes("-I/workspace/finn-hlslib")
        builder.append_includes("-I{}/include".format(os.environ["VIVADO_PATH"]))
        builder.append_includes("--std=c++11")
        builder.append_sources(code_gen_dir + "/*.cpp")
        builder.append_sources("/workspace/cnpy/cnpy.cpp")
        builder.append_includes("-lz")
        builder.set_executable_path(code_gen_dir + "/node_model")
        builder.build(code_gen_dir)
        self.set_nodeattr("executable_path", builder.executable_path)

    def dynamic_input_to_npy(self, context, count):
        """Saves input (given context) into .npy files.

        Count indicates the number of inputs that have to be saved."""
        node = self.onnx_node
        code_gen_dir = self.get_nodeattr("code_gen_dir_npysim")
        if code_gen_dir == "":
            raise Exception(
                """
Found no codegen dir for this node, did you run the codegen_npysim transformation?
            """
            )
        # create a npy file for each input of the node (in_ind is input index)
        # assuming dynamic inputs start from 0
        for in_ind in range(count):
            current_input_name = node.input[in_ind]
            np.save(
                os.path.join(code_gen_dir, "input_{}.npy".format(in_ind)),
                context[current_input_name],
            )

    def npy_to_dynamic_output(self, context):
        """Reads the output from a .npy file and saves it at the right place in
        the context dictionary."""
        # TODO support multi-output nodes as needed
        node = self.onnx_node
        code_gen_dir = self.get_nodeattr("code_gen_dir_npysim")
        output = np.load("{}/output.npy".format(code_gen_dir))
        context[node.output[0]] = output

    def exec_precompiled_singlenode_model(self):
        """Executes precompiled executable."""
        executable_path = self.get_nodeattr("executable_path")
        if executable_path == "":
            raise Exception(
                """
Found no executable for this node, did you run the codegen and
compilation transformations?
            """
            )
        process_execute = subprocess.Popen(executable_path, stdout=subprocess.PIPE)
        process_execute.communicate()

    def reset_rtlsim(self, sim):
        """Sets reset input in pyverilator to zero, toggles the clock and set it
        back to one"""
        sim.io.ap_rst_n = 0
        sim.io.ap_clk = 1
        sim.io.ap_clk = 0
        sim.io.ap_rst_n = 1

    def toggle_clk(self, sim):
        """Toggles the clock input in pyverilator once."""
        sim.io.ap_clk = 1
        sim.io.ap_clk = 0

    def rtlsim(self, sim, inp):
        """Runs the pyverilator simulation by passing the input values to the simulation,
        toggle the clock and observing the execution time. Function contains also an
        observation loop that can abort the simulation if no output value is produced
        after 100 cycles."""

        trace_file = self.get_nodeattr("rtlsim_trace")
        if trace_file != "":
            if trace_file == "default":
                trace_file = self.onnx_node.name + ".vcd"
            sim.start_vcd_trace(trace_file)
        inputs = inp
        outputs = []
        sim.io.out_V_V_TREADY = 1

        # observe if output is completely calculated
        # observation_count will contain the number of cycles the calculation ran
        num_out_values = self.get_number_output_values()
        output_observed = False
        observation_count = 0

        # avoid infinite looping of simulation by aborting when there is no change in
        # output values after 100 cycles
        no_change_count = 0
        old_outputs = outputs
        liveness_threshold = pyverilate_get_liveness_threshold_cycles()

        while not (output_observed):
            sim.io.in0_V_V_TVALID = 1 if len(inputs) > 0 else 0
            sim.io.in0_V_V_TDATA = inputs[0] if len(inputs) > 0 else 0
            if sim.io.in0_V_V_TREADY == 1 and sim.io.in0_V_V_TVALID == 1:
                inputs = inputs[1:]
            if sim.io.out_V_V_TVALID == 1 and sim.io.out_V_V_TREADY == 1:
                outputs = outputs + [sim.io.out_V_V_TDATA]
            sim.io.ap_clk = 1
            sim.io.ap_clk = 0

            observation_count = observation_count + 1
            no_change_count = no_change_count + 1

            if len(outputs) == num_out_values:
                self.set_nodeattr("sim_cycles", observation_count)
                output_observed = True

            if no_change_count == liveness_threshold:
                if old_outputs == outputs:
                    if trace_file != "":
                        sim.flush_vcd_trace()
                        sim.stop_vcd_trace()
                    raise Exception(
                        "Error in simulation! Takes too long to produce output. "
                        "Consider setting the LIVENESS_THRESHOLD env.var. to a "
                        "larger value."
                    )
                else:
                    no_change_count = 0
                    old_outputs = outputs
        if trace_file != "":
            sim.flush_vcd_trace()
            sim.stop_vcd_trace()
        return outputs

    def execute_node(self, context, graph):
        """Executes single node using npysim or rtlsim."""
        mode = self.get_nodeattr("exec_mode")
        if mode == "npysim":
            # save input(s)
            self.dynamic_input_to_npy(context, 1)
            # execute the precompiled model
            self.exec_precompiled_singlenode_model()
            # load output npy file
            self.npy_to_dynamic_output(context)
        elif mode == "rtlsim":
            pass

        else:
            raise Exception(
                """Invalid value for attribute exec_mode! Is currently set to: {}
            has to be set to one of the following value ("npysim", "rtlsim")""".format(
                    mode
                )
            )

    def generate_params(self, model, path):
        """Function to generate parameters (i.e. weights and thresholds),
        is member function of HLSCustomOp class but has to be filled
        by every node."""
        pass

    @abstractmethod
    def get_number_output_values(self):
        """Function to get the number of expected output values,
        is member function of HLSCustomOp class but has to be filled
        by every node."""
        pass

    @abstractmethod
    def global_includes(self):
        """Function to set the global includes for c++ code that has to be generated
        for npysim or rtlsim, is member function of HLSCustomOp class but has to
        be filled by every node."""
        pass

    @abstractmethod
    def defines(self, var):
        """Function to set the define commands for c++ code that has to be generated
        for npysim or rtlsim, is member function of HLSCustomOp class but has to
        be filled by every node.

        var: makes it possible to reuse the function for different c++ code generation.
        I.e. if set to "ipgen" in StreamingFCLayer_Batch additional PRAGMA defines are
        added."""
        pass

    @abstractmethod
    def read_npy_data(self):
        """Function to generate the commands for reading data from .npy file in c++,
        is member function of HLSCustomOp class but has to be filled by every node."""
        pass

    @abstractmethod
    def strm_decl(self):
        """Function to generate the commands for the stream declaration in c++,
        is member function of HLSCustomOp class but has to be filled
        by every node."""
        pass

    @abstractmethod
    def docompute(self):
        """Function to generate the commands for the computational part of the
        c++ code, is member function of HLSCustomOp class but has to be filled
        by every node."""
        pass

    @abstractmethod
    def dataoutstrm(self):
        """Function to generate the commands for reading out data from c++ and convert
        into npy format, is member function of HLSCustomOp class but has to be filled
        by every node."""
        pass

    @abstractmethod
    def save_as_npy(self):
        """Function to generate the commands for saving data in .npy file in c++,
        is member function of HLSCustomOp class but has to be filled by every node."""
        pass

    @abstractmethod
    def blackboxfunction(self):
        """Function to generate a blackbock function in c++ from which an IP block
        will be generated, is member function of HLSCustomOp class but has to be filled
        by every node."""
        pass

    @abstractmethod
    def pragmas(self):
        """Function to generate the pragma commands in c++, is member function of
        HLSCustomOp class but has to be filled by every node."""
        pass

    def get_folded_input_shape(self):
        """Returns folded input shape (according to synapse folding), if implemented."""
        raise Exception("get_folded_input_shape not implemented for this op")

    def get_folded_output_shape(self):
        """Returns folded output shape (according to neuron folding), if implemented."""
        raise Exception("get_folded_output_shape not implemented for this op")

    def get_instream_width(self):
        """Returns input stream width, if implemented."""
        raise Exception("get_instream_width not implemented for this op")

    def get_outstream_width(self):
        """Returns output stream width, if implemented."""
        raise Exception("get_outstream_width not implemented for this op")
