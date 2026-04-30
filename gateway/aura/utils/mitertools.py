from itertools import islice
from typing import Generator, Iterable, Tuple, TypeVar


T = TypeVar('T')
def batched(
    iterable: Iterable[T], n: int, *, strict: bool = False
) -> Generator[Tuple[T, ...], None, None]:
    if n < 1:
        raise ValueError('n must be at least one')
    iterator = iter(iterable)
    while batch := tuple(islice(iterator, n)):
        if strict and len(batch) != n:
            raise ValueError('batched(): incomplete batch')
        yield batch