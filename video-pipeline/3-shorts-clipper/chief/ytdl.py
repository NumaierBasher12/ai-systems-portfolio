"""yt-dlp behind Norton: trust the OS certificate store (Norton re-signs HTTPS), then run yt-dlp with the given args.
Copy of C:\\dev\\clipping\\yt.py so this app has no dependency on the clipping folder."""
import sys

import truststore

truststore.inject_into_ssl()
import yt_dlp  # noqa: E402

yt_dlp.main(sys.argv[1:])
