"""
Concatenated parts reader — several files read as the one file they were split from.

A vendor that splits one archive into byte-range parts ("download all parts, concatenate them, then
extract") leaves a choice: write the joined file once more, or read the parts as if they were
joined. For a 28 GB archive the copy costs as much disk again and a long write on a filesystem
where every operation is a remote call. This reader joins them in place: seekable, so `zipfile`
reads the central directory at the end and streams any member out of the middle.
"""

import io
from pathlib import Path
from typing import BinaryIO, Dict, List


class ConcatenatedPartsReader(io.RawIOBase):
    """
    A read-only, seekable view over files that together form one byte stream.

    Args:
        paths: The parts, in order
    """

    def __init__(self, paths: List[Path]):
        super().__init__()
        self._paths = list(paths)
        self._starts: List[int] = []
        total = 0
        for path in self._paths:
            self._starts.append(total)
            total += path.stat().st_size
        self._total = total
        self._position = 0
        self._open: Dict[int, BinaryIO] = {}

    def readable(self) -> bool:
        """
        Returns:
            Always True
        """
        return True

    def seekable(self) -> bool:
        """
        Returns:
            Always True — what lets a zip reader find the central directory at the end
        """
        return True

    def tell(self) -> int:
        """
        Returns:
            The absolute read position
        """
        return self._position

    def seek(self, offset: int, whence: int = io.SEEK_SET) -> int:
        """
        Move the read position.

        Args:
            offset: Offset relative to `whence`
            whence: SEEK_SET, SEEK_CUR or SEEK_END

        Returns:
            The new absolute position
        """
        if whence == io.SEEK_SET:
            self._position = offset
        elif whence == io.SEEK_CUR:
            self._position += offset
        else:
            self._position = self._total + offset
        return self._position

    def readinto(self, buffer) -> int:
        """
        Fill `buffer` from the current position, crossing part boundaries as needed.

        Args:
            buffer: Writable buffer

        Returns:
            Bytes read, 0 at the end of the last part
        """
        view = memoryview(buffer)
        done = 0
        while done < len(view) and self._position < self._total:
            index = self._part_at(self._position)
            part_end = self._starts[index + 1] if index + 1 < len(self._starts) else self._total
            handle = self._handle(index)
            handle.seek(self._position - self._starts[index])
            wanted = min(len(view) - done, part_end - self._position)
            chunk = handle.read(wanted)
            if not chunk:
                break
            view[done:done + len(chunk)] = chunk
            done += len(chunk)
            self._position += len(chunk)
        return done

    def close(self) -> None:
        """Close every part opened so far."""
        for handle in self._open.values():
            handle.close()
        self._open.clear()
        super().close()

    def _part_at(self, position: int) -> int:
        """
        Index of the part holding an absolute position.

        Args:
            position: Absolute byte position

        Returns:
            Part index
        """
        index = 0
        for candidate, start in enumerate(self._starts):
            if start > position:
                break
            index = candidate
        return index

    def _handle(self, index: int) -> BinaryIO:
        """
        The open file of one part, opened on first use.

        Args:
            index: Part index

        Returns:
            Its binary handle
        """
        if index not in self._open:
            self._open[index] = open(self._paths[index], 'rb')
        return self._open[index]
