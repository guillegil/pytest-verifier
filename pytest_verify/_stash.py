from __future__ import annotations

from typing import List

from pytest import StashKey

from ._descriptors import CheckDescriptor

#: The checks recorded for a test item, in order (``get_check_results`` reads it).
check_results_key: StashKey[List[CheckDescriptor]] = StashKey()
