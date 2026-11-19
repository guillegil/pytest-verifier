# Register values and bit fields

For integers that are read as hex or binary: register read-backs, status words, addresses,
masks. Nothing here changes a verdict except `bits`, which is its own comparison.

## `radix=` and `width=`

`equal`, `not_equal`, `greater`, `greater_equal`, `less`, `less_equal`, `between` and `bits`
take `radix=None` (`"hex"`, `"bin"`, `"oct"` or `"dec"`) and `width=None` (bits, 1 to 1024),
both keyword-only.

```python
verify.equal(rdata, 0x00F3, radix="hex", width=16, name="CTRL")
# expected 0x00F3, got 0x00F0; bits [1:0] differ
verify.less(addr, 0x100, radix="hex", width=12, name="ADDR")
# expected < 0x100, got 0x1F0
verify.between(code, 0b1000, 0b1100, radix="bin", width=4, name="Code")
# expected [0b1000, 0b1100], got 0b0101
```

- Hex and binary are padded to the width and grouped in fours (`0xDEAD_BEEF`,
  `0b0000_1111`); octal is padded (`0o000363`); `"dec"` is the plain number. `width` alone
  means hex; `radix` alone uses the digits the value needs. A negative value has a sign
  (`-0x000F`); a value wider than `width` shows every digit and is not an error.
- Only operands that are an `int` (not a `bool`) are formatted. A float, text or `None` in
  the same check is shown as usual, and the verdict is the plain comparison either way. So
  pass the integer, not `hex(value)` text.
- A failed `equal` adds which bits differ, highest first: `bits [7:6] and bits [1:0] differ`,
  `bit 4 differs`. The other checks only format their values. A negative operand gives no bit
  list.
- `units` still follow each value. An unknown radix, or a width that is not an `int` from 1
  to 1024, raises at the call (`ValueError`, or `TypeError` for a wrong type).

## `verify.bits`

`bits(actual, expected, *, mask=None, fields=None, width=None, radix=None, name=None,
note=None, actual_name=None, expected_name=None)` passes when
`(actual ^ expected) & mask == 0`; without `mask` every bit counts. It is shown in hex unless
`radix` says otherwise.

```python
verify.bits(rdata, wdata, mask=0x00FF, fields={"EN": 0, "MODE": (3, 1)}, width=16, name="CTRL")
# description: Verify 'CTRL' & 0x00FF == 0x00F3
# failed: expected 0x00F3, got 0x12F0 (mask 0x00FF); bits [1:0] differ: EN [0] is 0b0,
#         expected 0b1; MODE [3:1] is 0b000, expected 0b001
```

- `fields` maps a name to a bit index or to `(msb, lsb)` (either order). Fields may overlap.
  A failure names each field that differs under the mask, in binary up to four bits and in
  hex above; differing bits outside every field are still in the bit list.
- Both operands must be integers. A `bool`, float, text or `None` does not raise: the check
  fails with an `error` (`TypeError: bits compares integers, and actual is a float; ...`).
  Convert with `int()`.
- A negative `mask`, a field outside `width`, a blank or repeated field name, or `fields`
  that is not a mapping raises at the call.
- `equal` and the other checks take no `mask=` or `fields=`: use `bits`, or give the value's
  class a `__verify_format__` (below).

## Values that carry their format

A class whose instances stand for a register defines `__verify_format__`; its checks then
need no `radix=`, `width=` or `fields=`:

```python
from pytest_verifier import ValueFormat

class Reading(int):
    """One read of a register: an int that knows how the register is shown."""

    register: "Register"

    def __verify_format__(self) -> ValueFormat:
        return {"radix": "hex", "width": self.register.width, "fields": self.register.fields}

class Register:
    def __init__(self, bus, address, width=16, fields=None):
        self.bus, self.address, self.width, self.fields = bus, address, width, fields or {}

    def read(self) -> Reading:
        reading = Reading(self.bus.read(self.address))
        reading.register = self
        return reading

def test_ctrl(verify, bus):
    ctrl = Register(bus, 0x10, fields={"EN": 0, "MODE": (3, 1)})
    verify.equal(ctrl.read(), 0x00F3)
    # expected 0x00F3, got 0x00F0; bits [1:0] differ: EN [0] is 0b0, expected 0b1; MODE ...
```

- Read the hardware once per check, as above. For an object that is not an `int`,
  `__verify_format__` (its `value`) is called when the check is made and its `==` when the
  check is judged: both must use the same stored reading, never a bus read each, or the
  value shown is not the value compared.

- The method returns a mapping with any of `radix`, `width`, `fields` and `value` (all
  optional), or `None` for no format. `ValueFormat` is the `TypedDict` of that mapping and
  `VerifyFormat` a `typing.Protocol` for such classes; both come from `pytest_verifier`.
- It is called once per operand when the check is made, on the checked value first, then on
  the value it is compared with. The first answer gives the format; `radix=`, `width=` and
  `fields=` at the call win, each on its own. It works on the eight checks above. Fields
  must fit the width in force: `fields=` beyond the value's width raises `ValueError`, and
  under a narrower `width=` the value's fields outside it are left out.
- `value` is the integer the object stands for. Without it an `int` subclass is its own value
  and an object with `__index__` gives that. `equal` and the ordering checks still compare the
  object itself (its `==`, `<`), so define those; `bits` compares the integers. The record
  stores the integer in place of the object's `repr`.
- A method that raises or returns something invalid never breaks the test: the check is made
  without it, and its record has `format_error` (`"Register.__verify_format__: ValueError:
  ..."`). Arguments given at the call still apply.

## Limits tables

In a `verify.limits` table, `radix` and `width` are row arguments of the seven checks above,
and `"check": "bits"` makes a `bits` row with `expected` (an `int`) and optionally `mask`,
`fields`, `width` and `radix`:

```python
LIMITS = {
    "CTRL": {"check": "bits", "expected": 0x00F3, "mask": 0x00FF, "fields": {"EN": 0}, "width": 16},
    "STATUS": {"expected": 0x8000, "radix": "hex", "width": 16},
}
verify.limits({"CTRL": dut.read(0x10), "STATUS": dut.read(0x14)}, LIMITS)
```

In a `load_limits` CSV file, `radix`, `width`, `mask` and `fields` are columns. `fields` is
written `EN=0 MODE=3:1`: a name with a bit index or `msb:lsb`, spaces between fields. Whole
numbers may be written in hex (`0x00F3`), also grouped as the plugin prints them
(`0xDEAD_BEEF`, `0b1111_0011`).

## In records and summaries

A record with a format has `radix`, `width` (when given), `fields` (each as `[msb, lsb]`),
`mask` (`bits`), `rendered` (the formatted text of each integer operand, without units) and,
on an `equal` or `bits` whose integers differ, `bit_diff`. A recorded check that passed never
has `bit_diff` (a register object's `==` may accept another integer); a `checks` descriptor
that was not judged has it whenever the integers differ, so read it only with `passed` false.
The keys are described in [results.md](results.md). A formatted value longer than 240
characters has `...` for its middle digits in the description and detail; `rendered` is
whole. `--verify-summary=stats` prints a name's range in its format (`0x00F0 to 0x00F3`) and
a whole margin in the same radix (`margin 0xC`), and no range when its checks used different
formats or one measured a value that is not an integer.
