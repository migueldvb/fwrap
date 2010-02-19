import inspect
from fwrap_src import pyf_iface as pyf

class SourceGenerator(object):

    def __init__(self, basename):
        self.basename = basename
        self.filename = self._fname_template % basename

    def generate(self, program_unit_list, buf):
        raise NotImplementedError()

class GenPxd(SourceGenerator):

    _fname_template = "%s_c.pxd"

    def generate(self, program_unit_list, buf):
        buf.write('''
cdef extern from "config.h":
    ctypedef int fwrap_default_int

cdef extern:
    fwrap_default_int empty_func_c()
'''
    )

class GenCHeader(SourceGenerator):

    _fname_template = "%s_c.h"

    def generate(self, program_unit_list, buf):
        buf.write('''
#include "config.h"
fwrap_default_int empty_func_c();
'''
    )

class BasicVisitor(object):
    """A generic visitor base class which can be used for visiting any kind of object."""
    def __init__(self):
        self.dispatch_table = {}

    def visit(self, obj):
        cls = type(obj)
        try:
            handler_method = self.dispatch_table[cls]
        except KeyError:
            #print "Cache miss for class %s in visitor %s" % (
            #    cls.__name__, type(self).__name__)
            # Must resolve, try entire hierarchy
            pattern = "visit_%s"
            mro = inspect.getmro(cls)
            handler_method = None
            for mro_cls in mro:
                if hasattr(self, pattern % mro_cls.__name__):
                    handler_method = getattr(self, pattern % mro_cls.__name__)
                    break
            if handler_method is None:
                print type(self), type(obj)
                if hasattr(self, 'access_path') and self.access_path:
                    print self.access_path
                    if self.access_path:
                        print self.access_path[-1][0].pos
                        print self.access_path[-1][0].__dict__
                raise RuntimeError("Visitor does not accept object: %s" % obj)
            #print "Caching " + cls.__name__
            self.dispatch_table[cls] = handler_method
        return handler_method(obj)

class TreeVisitor(BasicVisitor):
    """
    Base class for writing visitors for a Cython tree, contains utilities for
    recursing such trees using visitors. Each node is
    expected to have a content iterable containing the names of attributes
    containing child nodes or lists of child nodes. Lists are not considered
    part of the tree structure (i.e. contained nodes are considered direct
    children of the parent node).
    
    visit_children visits each of the children of a given node (see the visit_children
    documentation). When recursing the tree using visit_children, an attribute
    access_path is maintained which gives information about the current location
    in the tree as a stack of tuples: (parent_node, attrname, index), representing
    the node, attribute and optional list index that was taken in each step in the path to
    the current node.
    
    Example:
    
    >>> class SampleNode(object):
    ...     child_attrs = ["head", "body"]
    ...     def __init__(self, value, head=None, body=None):
    ...         self.value = value
    ...         self.head = head
    ...         self.body = body
    ...     def __repr__(self): return "SampleNode(%s)" % self.value
    ...
    >>> tree = SampleNode(0, SampleNode(1), [SampleNode(2), SampleNode(3)])
    >>> class MyVisitor(TreeVisitor):
    ...     def visit_SampleNode(self, node):
    ...         print "in", node.value, self.access_path
    ...         self.visitchildren(node)
    ...         print "out", node.value
    ...
    >>> MyVisitor().visit(tree)
    in 0 []
    in 1 [(SampleNode(0), 'head', None)]
    out 1
    in 2 [(SampleNode(0), 'body', 0)]
    out 2
    in 3 [(SampleNode(0), 'body', 1)]
    out 3
    out 0
    """
    
    def __init__(self):
        super(TreeVisitor, self).__init__()
        self.access_path = []

    def __call__(self, tree):
        self.visit(tree)
        return tree

    def visitchild(self, child, parent, idx):
        self.access_path.append((parent, idx))
        result = self.visit(child)
        self.access_path.pop()
        return result

    def visitchildren(self, parent, attrs=None):
        """
        Visits the children of the given parent. If parent is None, returns
        immediately (returning None).
        
        The return value is a dictionary giving the results for each
        child (mapping the attribute name to either the return value
        or a list of return values (in the case of multiple children
        in an attribute)).
        """

        if parent is None: return None
        content = getattr(parent, 'content', None)
        if content is None or not isinstance(content, list):
            return None
        result = [self.visitchild(child, parent, idx) for (idx, child) in \
                enumerate(content)]
        return result

class FortranGen(TreeVisitor):

    def __init__(self, buf):
        super(FortranGen, self).__init__()
        self.buf = buf

    def generate(self, node):
        self.visit(node)

    def procedure_end(self, node):
        return "end %s %s" % (node.kind, node.name)

    def return_spec(self, node):
        return node.return_arg.declaration()

    def visit_ProcArgument(self, node):
        self.visit(node.proc)

    def visit_Argument(self, node):
        self.buf.putln(node.declaration())

    def proc_preamble(self, node):
        buf = self.buf
        buf.putln('use config')
        buf.putln('implicit none')
        for arg in node.args:
            self.visit(arg)
        if isinstance(node, pyf.Function):
            buf.putln(self.return_spec(node))

class FortranWrapperGen(FortranGen):

    def arg_list(self, args):
        return ', '.join([arg.name for arg in args])

    def procedure_decl(self, node):
        return '%s %s(%s) bind(c, name="%s")' % \
                (node.kind, node.name,
                        ', '.join(node.extern_arg_list()),
                        node.name)

    def declare_temps(self, node):
        for decl in node.temp_declarations():
            self.buf.putln(decl)

    def pre_call(self, node):
        for line in node.pre_call_code():
            self.buf.putln(line)

    def proc_call(self, node):
        proc_call = "%s(%s)" % (node.wrapped.name,
                                ', '.join(node.call_arg_list()))
        if isinstance(node, pyf.SubroutineWrapper):
            self.buf.putln("call %s" % proc_call)
        elif isinstance(node, pyf.FunctionWrapper):
            self.buf.putln("%s = %s" % (node.proc_result_name(), proc_call))

    def post_call(self, node):
        for line in node.post_call_code():
            self.buf.putln(line)

    def visit_ProcWrapper(self, node):
        buf = self.buf
        buf.putln(self.procedure_decl(node))
        buf.indent()
        self.proc_preamble(node)
        FortranInterfaceGen(buf).generate(node.wrapped)
        self.declare_temps(node)
        self.pre_call(node)
        self.proc_call(node)
        self.post_call(node)
        buf.dedent()
        buf.putln(self.procedure_end(node))

    def proc_preamble(self, node):
        buf = self.buf
        buf.putln('use config')
        buf.putln('implicit none')
        for decl in node.arg_declarations():
            buf.putln(decl)

class FortranInterfaceGen(FortranGen):

    def procedure_decl(self, node):
        return "%s %s(%s)" % (node.kind, node.name,
                    ', '.join([arg.name for arg in node.args]))

    def visit_Procedure(self, node):
        buf = self.buf
        buf.putln('interface')
        buf.indent()
        buf.putln(self.procedure_decl(node))
        buf.indent()
        self.proc_preamble(node)
        buf.dedent()
        buf.putln(self.procedure_end(node))
        buf.dedent()
        buf.putln('end interface')
