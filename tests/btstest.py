#!/usr/bin/env python3

"""
Grab input log.
"""

import argparse
import contextlib
import decimal
import json
import logging
import os
import re
import shutil
import socket
import subprocess
import sys
import time
import urllib.parse
import urllib.request

@contextlib.contextmanager
def add_to_sys_path(path_additions):
    old_path = sys.path[:]
    new_path = sys.path + path_additions
    sys.path = new_path
    yield
    sys.path = old_path
    return

class ParseError(Exception):
    pass

class RPCClient(object):
    def __init__(self, credentials):

        self.password_mgr = urllib.request.HTTPPasswordMgrWithDefaultRealm()

        self.rpc_url = "http://{host}:{port}/rpc".format(**credentials)
        self.password_mgr.add_password(None, self.rpc_url, credentials["rpc_user"], credentials["rpc_password"])

        self.basic_auth_handler = urllib.request.HTTPBasicAuthHandler(self.password_mgr)

        # create "opener" (OpenerDirector instance)
        self.url_opener = urllib.request.build_opener(self.basic_auth_handler)

        self.next_request_id = 1

        return

    def call(self, method, *args):
        logging.debug("calling URL:", self.rpc_url)
        # use the opener to fetch a URL
        o = { "method" : method, "params" : args, "id" : self.get_next_id() }
        logging.debug("data:", o)
        #post_data = urllib.parse.urlencode(o).encode("UTF-8")
        post_data = json.dumps(o).encode("UTF-8")
        req = urllib.request.Request(self.rpc_url, post_data)
        response = self.url_opener.open(req)
        response_content = response.read().decode("UTF-8")
        d = json.loads(response_content, parse_float=decimal.Decimal)
        logging.debug("result:", d)
        result = d["result"]
        logging.debug("result:", result)
        return result

    def get_next_request_id(self):
        result = self.next_request_id
        self.next_request_id = result+1
        return result

    def get_next_id(self):
        result = self.next_request_id
        self.next_request_id = result+1
        return result

    def __call__(self, method, *args):
        return self.call(method, args)

    def wait_for_rpc(self):
        #
        # retry connecting until we succeed or timeout is reached
        #

        retry_loop_start = time.time()

        while True:
            try:
                self("get_info")
            except Exception as e:
                # TODO: exception type
                logging.debug("can't connect:")
                logging.debug(e)
                now = time.time()
                dt = now - retry_loop_start
                if dt < 10:
                    time.sleep(0.25)
                elif dt < 20:
                    time.sleep(1)
                elif dt < 60:
                    time.sleep(10)
                elif dt < 60*5:
                    time.sleep(15)
                else:
                    logging.debug("timed out")
                    # TODO : exception type
                    raise RuntimeError()
                continue
            break

        dt = time.time() - retry_loop_start
        logging.info("succeeded connecting to HTTP RPC after {} seconds".format(dt))
        return

class PortAssigner(object):
    def __init__(self, min_port=30000, max_port=40000):
        self.min_port = min_port
        self.max_port = max_port
        self.next_port = min_port
        return

    def __call__(self):
        result = self.next_port
        self.next_port = self.next_port+1
        if self.next_port >= self.max_port:
            self.next_port = self.min_port
        return self.next_port

class LinuxPortAssigner(PortAssigner):
    #
    # this class queries the Linux kernel for ports in use
    # and avoids those that are currently in use
    #
    # it works on any system that provides /proc/net/tcp in Linux format
    #
    # if the file does not exist, it doesn't check ports are in use,
    # so this class should still be safe to use on non-Linux OS
    #

    def __init__(self, min_port=30000, max_port=40000):
        PortAssigner.__init__(self, min_port, max_port)
        return

    def __call__(self):
        if not os.path.exists("/proc/net/tcp"):
            return PortAssigner.__call__(self)
        ports_in_use = set()
        with open("/proc/net/tcp", "r") as f:
            for line in f:
                u = line.split()
                if len(u) < 3:
                    continue
                if not u[2].endswith(":0000"):
                    continue
                port_in_use = u[1][-5:]
                if not port_in_use.startswith(":"):
                    continue
                portnum = int(port_in_use[1:], 16)
                ports_in_use.add(portnum)
        while True:
            port = PortAssigner.__call__(self)
            if port not in ports_in_use:
                break
        return port

class ClientProcess(object):

    default_client_exe = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "programs", "client", "bitsharestestnet_client"))
    default_rpc_port = LinuxPortAssigner()
    default_http_port = default_rpc_port
    default_p2p_port = default_rpc_port
    default_username = "username"
    default_password = "password"
    default_testdir = os.path.join(os.path.dirname(__file__), "btstests", "out")
    default_genesis_config = os.path.join(os.path.dirname(__file__), "drltc_tests", "genesis.json")

    def __init__(self,
        name="default",
        client_exe=None,
        p2p_port=None,
        rpc_port=None,
        http_port=None,
        username=None,
        password=None,
        genesis_config=None,
        testdir=None,
        rpc_client=None,
        ):
        self.name = name

        if client_exe is None:
            client_exe = self.default_client_exe
        self.client_exe = client_exe

        if p2p_port is None:
            p2p_port = self.default_p2p_port
        self.p2p_port = p2p_port

        if rpc_port is None:
            rpc_port = self.default_rpc_port
        self.rpc_port = rpc_port

        if http_port is None:
            http_port = self.default_http_port
        self.http_port = http_port

        if username is None:
            username = self.default_username
        self.username = username

        if password is None:
            password = self.default_password
        self.password = password

        if genesis_config is None:
            genesis_config = self.default_genesis_config
        self.genesis_config = genesis_config

        if testdir is None:
            testdir = self.default_testdir
        self.testdir = testdir

        self.process_object = None

        self.stdout_file = None
        self.stderr_file = None

        self.rpc_client = rpc_client

        return

    def __enter__(self):
        self.start()
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.stop()
        return

    def start(self):
        # execute process object and wait for RPC to become available
        if self.process_object is not None:
            # TODO: exception type
            raise RuntimeError("start() called multiple times")

        data_dir = os.path.abspath(os.path.join(self.testdir, self.name))
        if not os.path.exists(self.testdir):
            os.makedirs(self.testdir)
        if os.path.exists(data_dir):
            shutil.rmtree(data_dir)
        os.makedirs(data_dir)

        if callable(self.p2p_port):
            self.p2p_port = self.p2p_port()

        if callable(self.rpc_port):
            self.rpc_port = self.rpc_port()

        if callable(self.http_port):
            self.http_port = self.http_port()

        genesis_config = os.path.abspath(self.genesis_config)

        args = [
            "--p2p-port", str(self.p2p_port),
            "--rpcuser", self.username,
            "--rpcpassword", self.password,
            "--rpcport", str(self.rpc_port),
            "--httpport", str(self.http_port),
            "--disable-default-peers",
            "--disable-peer-advertising",
            "--min-delegate-connection-count", "0",
            "--upnp", "0",
            "--genesis-config", genesis_config,
            "--data-dir", data_dir,
            "--server",
            #"--log-commands", os.path.join(data_dir, "console.log"),
            ]

        logging.debug("args: ", args)

        self.stdout_file = open(os.path.join(data_dir, "stdout.txt"), "wb")
        self.stderr_file = open(os.path.join(data_dir, "stderr.txt"), "wb")

        self.process_object = subprocess.Popen(
            [self.client_exe]+args,
            stdout=self.stdout_file,
            stderr=self.stderr_file,
            stdin=subprocess.PIPE,
            cwd=data_dir,
            )
        return

    def stop(self):
        if self.process_object is None:
            return
        if self.process_object.poll() is not None:
            # subprocess already exited, no cleanup is necessary
            return

        # send SIGTERM, then SIGKILL if we don't exit quickly (or cross-platform equivalents)
        self.process_object.terminate()
        try:
            self.process_object.wait(timeout=10)
        except subprocess.TimeoutExpired:
            self.process_object.kill()
            self.process_object.wait(timeout=10)

        if self.stdout_file is not None:
            self.stdout_file.close()
        if self.stderr_file is not None:
            self.stderr_file.close()

        return

class TestClient(object):
    def __init__(self, name="", rpc_client=None):
        self.last_command_output = ""
        self.last_command_pos = 0       # how far we've scanned the output
        self.last_command_failed = False
        self.name = name
        self.rpc_client = rpc_client
        self.re_cache = {}
        self.failure_count = 0
        return

    def expect_fail(self, s):
        dots = ""
        if len(s) > 40:
            s = s[:40]
            dots = " ..."
        m = self.last_command_pos
        logging.error("expected: "+repr(s)+dots)

        dots = ""
        got = self.last_command_output[m:]
        if len(got) > 40:
            got = got[:40]
            dots = " ..."
        logging.error("got     : "+repr(got)+dots)
        if not self.last_command_failed:
            self.last_command_failed = True
            self.failure_count += 1
        return

    def expect_str(self, s):
        m = self.last_command_pos
        n = m+len(s)
        if self.last_command_output[m:n] == s:
            self.last_command_pos = n
        else:
            self.expect_fail(s)
        return

    def expect_regex(self, regex):
        logging.debug("expecting regex:", regex)
        logging.debug("matching at {} against {}".format(self.last_command_pos, repr(self.last_command_output)))
        compiled_re = self.re_cache.get(regex)
        if compiled_re is None:
            compiled_re = re.compile(regex)
            self.re_cache[regex] = compiled_re

        m = compiled_re.match(self.last_command_output, self.last_command_pos)
        if m is not None:
            self.last_command_pos = m.end()
        else:
            self.expect_fail(regex)
        return m.groupdict()

    def reset_last_command(self, new_command_output=None):
        # echo command output
        print(new_command_output, end="", flush=True)
        self.last_command_output = new_command_output
        self.last_command_pos = 0
        self.last_command_failed = False
        return

    def execute_cmd(self, cmd, expect_enabled=True):
        if expect_enabled and (self.last_command_pos != len(self.last_command_output)):
            self.expect_fail("<end of command output>")

        # store result
        result = self.rpc_client.call("execute_command_line", cmd)
        self.reset_last_command(result)
        return

class Test(object):
    def __init__(self):
        self.context = {}
        self.name2client = {}
        self.context["active_client"] = ""
        self.context["expect_str"] = self.expect_str
        self.context["expect_regex"] = self.expect_regex
        self.context["run_testdir"] = self.run_testdir
        self.context["regex"] = self.expect_regex
        self.context["register_client"] = self.register_client
        self.context["expect_enabled"] = True
        self.context["_btstest"] = sys.modules[__name__]
        return

    def load_testenv(self, testenv_filename):
        testenv_filename = os.path.abspath(testenv_filename)
        with open(testenv_filename, "r") as f:
            testenv_script = f.read()
        compiled_testenv = compile(testenv_script, testenv_filename, "exec", dont_inherit=1)

        self.context["my_filename"] = testenv_filename
        self.context["my_path"] = os.path.dirname(testenv_filename)

        with add_to_sys_path([os.path.dirname(testenv_filename)]):
            exec(compiled_testenv, self.context)
        return

    def interpret_expr(self, expr, filename=""):
        expr = expr.strip()
        compiled_expr = compile(expr, filename, "eval")
        result = exec(compiled_expr, self.context)
        if isinstance(result, str):
            self.expect_str(result)
        return

    def split_line(self, line, filename=""):
        if filename == "":
            error_file_info = ""
        else:
            error_file_info = " in file "+filename
        start_pos = 0
        logging.debug("splitting line:", repr(line))
        while True:
            begin_tag = line.find("${", start_pos)
            end_tag = line.find("}$", start_pos)
            if begin_tag < 0:
                if end_tag < 0:
                    self.expect_str(line[start_pos:])
                    break
                raise ParseError("mismatched tag ('}$' without beginning '${')"+error_file_info)
            if end_tag < 0:
                raise ParseError("mismatched tag ('${' without ending '}$')"+error_file_info)
            if begin_tag > end_tag:
                raise ParseError("mismatched tag ('}$' without beginning '${')"+error_file_info)
            expr = line[begin_tag+2:end_tag]
            self.interpret_expr(expr)
            start_pos = end_tag+2
        return

    def get_active_client(self):
        return self.name2client[self.context["active_client"]]

    def expect_str(self, data):
        if not self.context["expect_enabled"]:
            return
        if data == "":
            return
        client = self.get_active_client()
        client.expect_str(data)
        return

    def expect_regex(self, regex):
        if not self.context["expect_enabled"]:
            return
        client = self.get_active_client()
        client.expect_regex(regex)
        # TODO:  write regex result
        return

    def parse_metacommand(self, cmd):
        cmd = cmd.split()
        if cmd[0] == "!client":
            self.context["active_client"] = cmd[1]
            return
        elif cmd[0] == "!expect":
            if cmd[1] == "enable":
                self.context["expect_enabled"] = True
            elif cmd[1] == "disable":
                self.context["expect_enabled"] = False
            else:
                raise RuntimeError("unknown keyword in expect command")
            return
        # TODO: exception type
        raise RuntimeError("unknown metacommand ", cmd)

    def execute_cmd(self, cmd):
        if cmd.startswith("!"):
            self.parse_metacommand(cmd)
            return

        client = self.get_active_client()
        client.execute_cmd(cmd, expect_enabled=self.context["expect_enabled"])
        return

    def parse_script(self, filename):
        with open(filename, "r") as f:
            for line in f:
                p = line.find(">>> ")
                if p < 0:
                    self.split_line(line)
                else:
                    # echo
                    print(line, end="", flush=True)
                    self.execute_cmd(line[p+4:])
        return

    def run_testdir(self, testdir):
        filenames = sorted(os.listdir(testdir))
        for f in filenames:
            if not f.endswith(".btstest"):
                continue
            self.parse_script(os.path.join(testdir, f))
        return

    def register_client(self, client=None):
        self.name2client[client.name] = client
        return

def main():
    parser = argparse.ArgumentParser(description="Testing the future of banking.")
    parser.add_argument("testdirs", metavar="TEST", type=str, nargs="+", help="Test directory")
    parser.add_argument("--testenv", metavar="ENV", type=str, action="append", help="Specify a global testenv")
    parser.add_argument("--loglevel", metavar="LOGLEVEL", type=str, default="INFO")
    
    args = parser.parse_args()

    numeric_loglevel = getattr(logging, args.loglevel.upper(), None)
    if not isinstance(numeric_loglevel, int):
        raise ValueError("Invalid log level {}".format(repr(numeric_loglevel)))
    logging.basicConfig(level=numeric_loglevel)

    for d in args.testdirs:
        local_testenv_filename = os.path.join(d, "testenv")
        if not os.path.exists(local_testenv_filename):
            logging.warn("test "+d+" does not appear to be a test, skipping")
            continue

        test = Test()
        # load all testenv's
        for testenv_filename in (args.testenv or []):
            test.load_testenv(testenv_filename)
        # and local testenv
        test.load_testenv(local_testenv_filename)

        # we're actually done, since local testenv actually runs the test!
    return

if __name__ == "__main__":
    main()
