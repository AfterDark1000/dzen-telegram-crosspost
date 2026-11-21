#!/usr/bin/env python3
"""One X dispatcher: either Buffer primary or Typefully backup, never both."""
import asyncio
import sys
import time

from buffer_x_check import BufferRateLimitError
from buffer_x_crosspost import main as buffer_main, record_rate_limit
from typefully_x_failover import FailoverError, main as typefully_main
from x_editorial_queue import load_queue


async def main():
    route = await typefully_main()
    if route == "primary":
        await buffer_main()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except BufferRateLimitError as exc:
        try:
            record_rate_limit(load_queue(), exc, int(time.time()))
        except Exception as state_error:
            print("X COOLDOWN STATE ERROR:", state_error, file=sys.stderr, flush=True)
        print("X PUBLISH ERROR:", exc, file=sys.stderr, flush=True)
        sys.exit(1)
    except (FailoverError, RuntimeError) as exc:
        print("X ROUTE HELD:", exc, file=sys.stderr, flush=True)
        sys.exit(1)
