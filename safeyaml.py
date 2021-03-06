#!/usr/bin/env python3

import re
import io
import sys

from collections import OrderedDict
    
whitespace = re.compile(r"(?:\ |\t|\r|\n)+")
        
comment = re.compile(r"(#[^\r\n]*(?:\r?\n|$))+")

int_b10 = re.compile(r"\d[\d]*")
flt_b10 = re.compile(r"\.[\d]+")
exp_b10 = re.compile(r"[eE](?:\+|-)?[\d+]")

string_dq = re.compile(
    r'"(?:[^"\\\n\x00-\x1F\uD800-\uDFFF]|\\(?:[\'"\\/bfnrt]|x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}))*"')
string_sq = re.compile(
    r"'(?:[^'\\\n\x00-\x1F\uD800-\uDFFF]|\\(?:[\"'\\/bfnrt]|x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|U[0-9a-fA-F]{8}))*'")

identifier = re.compile(r"(?!\d)[\w\.]+")

bareword = re.compile("(?:{}|{}|{}):".format(string_dq.pattern, string_sq.pattern, identifier.pattern))

str_escapes = {
    'b': '\b',
    'n': '\n',
    'f': '\f',
    'r': '\r',
    't': '\t',
    '/': '/',
    '"': '"',
    "'": "'",
    '\\': '\\',
}

builtin_names = {'null': None, 'true': True, 'false': False}

reserved_names = set("yes|no|on|off".split("|"))

class ParserErr(Exception):
    def __init__(self, buf, pos, reason=None):
        self.buf = buf
        self.pos = pos
        if reason is None:
            nl = buf.rfind(' ', pos - 10, pos)
            if nl < 0:
                nl = pos - 5
            reason = "Unknown Character {} (context: {})".format(
                repr(buf[pos]), repr(buf[pos - 10:pos + 5]))
        Exception.__init__(self, "{} (at pos={})".format(reason, pos))



def parse(buf, transform=None):
    pos = 1 if buf.startswith("\uFEFF") else 0

    output = io.StringIO()
    obj, pos = parse_structure(buf, pos, output, transform)

    m = whitespace.match(buf, pos)
    while m:
        pos = m.end()
        m = comment.match(buf, pos)
        if m:
            pos = m.end()
            m = whitespace.match(buf, pos)

    if pos != len(buf):
        raise ParserErr(buf, pos, "Trailing content: {}".format(
            repr(buf[pos:pos + 10])))

    return obj, output.getvalue()

def move_to_next(buf, pos):
    line_pos = pos
    next_line = False
    while pos < len(buf):
        peek = buf[pos]

        if peek == ' ':
            pos +=1
        elif peek == '\n' or peek == '\r':
            pos +=1
            line_pos = pos
            next_line = True
        elif peek == '#':
            next_line = True
            while pos < len(buf):
                pos +=1
                if buf[pos] == '\n':
                    line_pos = pos
                    break 
        else:
            break
    return pos, pos-line_pos, next_line

def parse_structure(buf, pos, output, transform, indent=0):
    start = pos
    pos, my_indent, next_line = move_to_next(buf, pos)

    if my_indent < indent:
        raise ParserErr(buf, pos, "Unexpected dedent")

    output.write(buf[start:pos])
    peek = buf[pos]
    
    if peek == '-':
        out = []
        while pos < len(buf):
            if buf[pos] != '-':
                break
            output.write("-")
            pos +=1
            if buf[pos] not in (' ', '\r','\n'):
                raise ParserErr(buf, pos, "Expected list item {}".format(repr(buf[pos:])))

            new_pos, new_indent, next_line = move_to_next(buf, pos)
            if next_line and new_indent <= my_indent:
                raise ParserErr(buf, new_pos, "Unexpected dedent")

            if not next_line:
                output.write(buf[pos:new_pos])
                obj, pos = parse_object(buf, new_pos, output, transform)
            else:
                obj, pos = parse_structure(buf, pos, output, transform, indent=my_indent)

            out.append(obj)

            new_pos, new_indent, next_line = move_to_next(buf, pos)
            if not next_line or new_indent != my_indent:
                break
            else:
                output.write(buf[pos:new_pos])
                pos = new_pos
                    
        return out, pos
    m = bareword.match(buf, pos)

    if peek == '"' or peek == '"' or m:
        out = OrderedDict()

        while pos < len(buf):
            m = bareword.match(buf, pos)
            if not m:
                break

            name, pos = parse_key(buf, pos, output, transform)
            if name in out:
                raise ParserErr(buf,pos, 'duplicate key: {}, {}'.format(name, out))

            if buf[pos] != ':':
                raise ParserErr(buf, pos, "Expected a ':' after a key".format(repr(buf[pos:])))
            output.write(":")
            pos +=1
            if buf[pos] not in (' ', '\r','\n'):
                raise ParserErr(buf, pos, "Expected space/newline after ':'".format(repr(buf[pos:])))

            new_pos, new_indent, next_line = move_to_next(buf, pos)
            if next_line and new_indent <= my_indent:
                raise ParserErr(buf, new_pos, "Unexpected dedent")

            if not next_line:
                output.write(buf[pos:new_pos])
                obj, pos = parse_object(buf, new_pos, output, transform)
            else:
                output.write(buf[pos:new_pos-new_indent])
                obj, pos = parse_structure(buf, new_pos-new_indent, output, transform, indent=my_indent)

            # dupe check
            out[name] = obj

            new_pos, new_indent, next_line = move_to_next(buf, pos)
            if not next_line or new_indent != my_indent:
                break
            else:
                output.write(buf[pos:new_pos])
                pos = new_pos
                    
        return out, pos

    if peek == '{' or peek == '[':
        return parse_object(buf, pos, output, transform)

    raise ParserErr(buf, pos, "No root object found: expected object or list")

def skip_whitespace(buf, pos, output):
    m = whitespace.match(buf, pos)
    while m:
        output.write(buf[pos:m.end()])
        pos = m.end()
        m = comment.match(buf, pos)
        if m:
            output.write(buf[pos:m.end()])
            pos = m.end()
            m = whitespace.match(buf, pos)
    return pos


def parse_object(buf, pos, output, transform=None):
    pos = skip_whitespace(buf, pos, output)

    peek = buf[pos]

    if peek == '{':
        output.write('{')
        out = OrderedDict()

        pos += 1
        pos = skip_whitespace(buf, pos, output)

        while buf[pos] != '}':
            
            key, new_pos = parse_key(buf, pos, output, transform)

            if key in out:
                raise ParserErr(buf,pos, 'duplicate key: {}, {}'.format(key, out))

            pos = skip_whitespace(buf, new_pos, output)

            peek = buf[pos]

            ### bare key check

            if peek == ':':
                output.write(':')
                pos += 1
                pos = skip_whitespace(buf, pos, output)
            else:
                raise ParserErr(
                    buf, pos, "Expected key:value pair but found {}".format(repr(peek)))

            item, pos = parse_object(buf, pos, output, transform)

            # dupe check
            out[key] = item

            pos = skip_whitespace(buf, pos, output)

            peek = buf[pos]
            if peek == ',':
                pos += 1
                output.write(',')
                pos = skip_whitespace(buf, pos, output)
            elif peek != '}':
                raise ParserErr(
                    buf, pos, "Expecting a ',', or a '{}' but found {}".format('}',repr(peek)))

        output.write('}')
        if transform is not None:
            out = transform(out)
        return out, pos + 1

    elif peek == '[':
        output.write("[")
        out = []

        pos += 1

        pos = skip_whitespace(buf, pos, output)

        while buf[pos] != ']':
            item, pos = parse_object(buf, pos, output, transform)
            out.append(item)

            pos = skip_whitespace(buf, pos, output)

            peek = buf[pos]
            if peek == ',':
                output.write(',')
                pos += 1
                pos = skip_whitespace(buf, pos, output)
            elif peek != ']':
                raise ParserErr(
                    buf, pos, "Expecting a ',', or a ']' but found {}".format(repr(peek)))

        output.write("]")
        pos += 1

        if transform is not None:
            out = transform(out)
        return out, pos

    elif peek == "'" or peek == '"':
        return parse_string(buf, pos, output, transform)
    elif peek in "-+0123456789":

        flt_end = None
        exp_end = None

        sign = +1

        start = pos

        if buf[pos] in "+-":
            if buf[pos] == "-":
                sign = -1
            pos += 1
        peek = buf[pos]

        leading_zero = (peek == '0')
        m = int_b10.match(buf, pos)
        if m:
            int_end = m.end()
            end = int_end
        else:
            raise ParserErr(buf, pos, "Invalid number")

        t = flt_b10.match(buf, end)
        if t:
            flt_end = t.end()
            end = flt_end

        e = exp_b10.match(buf, end)
        if e:
            exp_end = e.end()
            end = exp_end

        if flt_end or exp_end:
            out = sign * float(buf[pos:end])
        else:
            out = sign * int(buf[pos:end])
            if leading_zero and out != 0:
                raise Exception('Nope')

        output.write(buf[start:end])

        if transform is not None:
            out = transform(out)
        return out, end

    else:
        m = identifier.match(buf, pos)
        if m:
            end = m.end()
            item = buf[pos:end]
        else:
            raise ParserErr(buf, pos)


        if item.lower() not in builtin_names:
            raise ParserErr(
                buf, pos, "{} is not a recognised built-in".format(repr(item)))

        item = item.lower()
        out = builtin_names[item]
        output.write(item)

        if transform is not None:
            out = transform(out)
        return out, end

    raise ParserErr(buf, pos)


def parse_key(buf, pos, output, transform):
    m = identifier.match(buf, pos)
    if m:
        name = buf[pos:m.end()]
        if name.lower() in reserved_names:
            raise ParserErr(buf, pos,"Can't use {} as a bareword key".format(name))

        output.write(buf[pos:m.end()])
        pos = m.end()
        # ugh, hack
        if buf[pos+1] not in (' ', '\r','\n'):
            raise ParserErr(buf, pos, "Expected space/newline after ':', got {}".format(repr(buf[pos:])))
    else:
        name, pos = parse_string(buf, pos, output, transform)
    return name, pos

def parse_string(buf, pos, output, transform):
    s = io.StringIO()
    peek = buf[pos]

    # validate string
    if peek == "'":
        m = string_sq.match(buf, pos)
        if m:
            end = m.end()
            output.write(buf[pos:end])
        else:
            raise ParserErr(buf, pos, "Invalid single quoted string")
    else:
        m = string_dq.match(buf, pos)
        if m:
            end = m.end()
            output.write(buf[pos:end])
        else:
            raise ParserErr(buf, pos, "Invalid double quoted string")

    lo = pos + 1  # skip quotes
    while lo < end - 1:
        hi = buf.find("\\", lo, end)
        if hi == -1:
            s.write(buf[lo:end - 1])  # skip quote
            break

        s.write(buf[lo:hi])

        esc = buf[hi + 1]
        if esc in str_escapes:
            s.write(str_escapes[esc])
            lo = hi + 2
        elif esc == 'x':
            n = int(buf[hi + 2:hi + 4], 16)
            s.write(chr(n))
            lo = hi + 4
        elif esc == 'u':
            n = int(buf[hi + 2:hi + 6], 16)
            if 0xD800 <= n <= 0xDFFF:
                raise ParserErr(
                    buf, hi, 'string cannot have surrogate pairs')
            s.write(chr(n))
            lo = hi + 6
        elif esc == 'U':
            n = int(buf[hi + 2:hi + 10], 16)
            if 0xD800 <= n <= 0xDFFF:
                raise ParserErr(
                    buf, hi, 'string cannot have surrogate pairs')
            s.write(chr(n))
            lo = hi + 10
        # elif esc == '\n':
        #     lo = hi + 2
        #     while hi <  
        elif (buf[hi + 1:hi + 3] == '\r\n'):
            lo = hi + 3
        else:
            raise ParserErr(
                buf, hi, "Unkown escape character {}".format(repr(esc)))

    out = s.getvalue()

    # XXX output.write string.escape

    if transform is not None:
        out = transform(out)
    return out, end

if __name__ == '__main__':
    fh = sys.stdin
    obj, output = parse(fh.read())
    print(output)
