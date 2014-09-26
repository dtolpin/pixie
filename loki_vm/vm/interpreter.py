from loki_vm.vm.object import Object
import loki_vm.vm.code as code
import loki_vm.vm.numbers as numbers
from loki_vm.vm.primitives import nil, true, false
from rpython.rlib.rarithmetic import r_uint, intmask
from rpython.rlib.jit import JitDriver, promote, elidable, elidable_promote

def get_location(ip, sp, bc):
    return code.BYTECODES[bc[ip]]

jitdriver = JitDriver(greens=["ip", "sp", "bc"], reds=["frame"], virtualizables=["frame"],
                      get_printable_location=get_location)

class Frame(object):
    _virtualizable_ = [#"stack[*]",
                       "sp",
                       "ip",
                       "bc",
                       "consts",
                       "code_obj",
                       "argc",
                       "prev_frame"]
    def __init__(self, code_obj, prev_frame=None, handler=None):
        self.code_obj = code_obj
        self.sp = r_uint(0)
        self.ip = r_uint(0)
        self.stack = [None] * 24
        if code_obj is not None:
            self.unpack_code_obj()
        self.argc = 0
        self.prev_frame = prev_frame
        self.handler = handler

    def clone(self):
        frame = Frame(self.code_obj, self.prev_frame, self.handler)
        frame.sp = self.sp
        frame.ip = self.ip

        frame.stack = [None] * len(self.stack)
        x = 0
        #only copy what we need from the stack
        while x < self.sp:
            frame.stack[x] = self.stack[x]
            x += 1
        frame.argc = self.argc
        return frame

    def unpack_code_obj(self):
        self.bc = self.code_obj.get_bytecode()
        self.consts = self.code_obj.get_consts()

    def get_inst(self):
        assert 0 <= self.ip < len(self.bc)
        inst = self.bc[self.ip]
        self.ip = self.ip + 1
        return promote(inst)

    def push(self, val):
        #assert val is not None
        assert 0 <= self.sp < len(self.stack)
        #print type(self.sp), self.sp
        self.stack[self.sp] = val
        self.sp += 1

    def pop(self):
        #print type(self.sp), self.sp
        self.sp -= 1
        v = self.stack[self.sp]
        self.stack[self.sp] = None
        return v

    def pop_args(self):
        for x in range(self.argc):
            self.pop()

    def nth(self, delta):
        return self.stack[self.sp - delta - 1]

    def push_nth(self, delta):
        self.push(self.nth(delta))

    def push_n(self, args, argc):
        x = argc
        while x != 0:
            self.push(args[x - 1])
            x -= 1

    def pop_n(self, argc):
        args = [None] * argc
        x = r_uint(0)
        while x < argc:
            args[x] = self.pop()
            x += 1
        return args

    def push_const(self, idx):
        self.push(self.consts[idx])

    def jump_rel(self, delta):
        self.ip += delta - 1

    def slice_stack(self, on):
        frame = self
        top_frame = frame
        prev_frame = None
        while frame is not nil:
            if frame.handler is on:
                new_top_frame = frame
                frame = prev_frame
                frame.prev_frame = None
                return (new_top_frame, frame, top_frame)

            prev_frame = frame
            frame = frame.prev_frame


        raise ValueError()

    def make_frame(self, code_obj):
        return Frame(code_obj, self)

    def new(self, code_obj):
        return Frame(code_obj)



def interpret(code_obj):
    frame = Frame(code_obj)

    while True:
        jitdriver.jit_merge_point(bc=frame.bc,
                                  ip=frame.ip,
                                  sp=frame.sp,
                                  frame=frame)
        inst = frame.get_inst()

        #print code.BYTECODES[inst]

        if inst == code.LOAD_CONST:
            arg = frame.get_inst()
            frame.push_const(arg)
            continue

        if inst == code.ADD:
            a = frame.pop()
            b = frame.pop()

            r = numbers.add(a, b)
            frame.push(r)
            continue

        if inst == code.INSTALL:
            fn = frame.pop()
            handler = frame.pop()

            frame = Frame(None, frame, handler)
            frame.push(fn)
            frame = fn.invoke(frame, 1)

            continue

        if inst == code.INVOKE:
            argc = frame.get_inst()
            fn = frame.nth(argc - 1)

            assert isinstance(fn, code.BaseCode)

            frame = fn.invoke(frame, argc)

            continue

        if inst == code.TAIL_CALL:
            args = frame.get_inst()
            fn = frame.nth(args - 1)

            assert isinstance(fn, code.BaseCode)

            frame = fn.tail_call(frame, args)

            jitdriver.can_enter_jit(bc=frame.bc,
                                    frame=frame,
                                    sp=frame.sp,
                                    ip=frame.ip)
            continue

        if inst == code.DUP_NTH:
            arg = frame.get_inst()
            frame.push_nth(arg)

            continue

        if inst == code.RETURN:
            val = frame.pop()

            frame.pop_args()

            if frame.prev_frame is None:
                return val

            frame = frame.prev_frame
            if frame.handler is not None:
                frame = frame.prev_frame


            frame.push(val)

            continue

        if inst == code.COND_BR:
            v = frame.pop()
            loc = frame.get_inst()
            if v is not nil and v is not false:
                continue
            frame.jump_rel(loc)
            continue

        if inst == code.JMP:
            ip = frame.get_inst()
            frame.jump_rel(ip)
            continue

        if inst == code.EQ:
            a = frame.pop()
            b = frame.pop()
            frame.push(numbers.eq(a, b))
            continue

        if inst == code.MAKE_CLOSURE:
            argc = frame.get_inst()

            lst = [None] * argc

            for idx in range(argc - 1, -1, -1):
                lst[idx] = frame.pop()

            cobj = frame.pop()
            closure = code.Closure(cobj, lst)
            frame.push(closure)

            continue

        if inst == code.CLOSED_OVER:
            assert isinstance(frame.code_obj, code.Closure)
            idx = frame.get_inst()
            frame.push(frame.code_obj._closed_overs[idx])
            continue

        if inst == code.SET_VAR:
            val = frame.pop()
            var = frame.pop()

            assert isinstance(var, code.Var)
            var.set_root(val)
            frame.push(var)
            continue

        if inst == code.POP:
            frame.pop()
            continue

        if inst == code.DEREF_VAR:
            var = frame.pop()
            assert isinstance(var, code.Var)
            frame.push(var.deref())
            continue

        print "NO DISPATCH FOR: " + code.BYTECODES[inst]
        raise Exception()

