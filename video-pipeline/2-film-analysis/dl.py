# Download one video for analysis: best video+audio, subtitles, info json.
# usage: py -3.13 dl.py <url> <out_dir>
import sys, glob, os
import truststore
truststore.inject_into_ssl()  # Norton re-signs HTTPS; use the Windows cert store
import yt_dlp

url, out = sys.argv[1], sys.argv[2]
ffmpeg_dir = os.path.dirname(glob.glob(os.path.expandvars(
    r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_*\ffmpeg-*\bin\ffmpeg.exe"))[0])

sys.exit(yt_dlp.main([
    url,
    "--cookies", os.path.join(os.path.dirname(os.path.abspath(__file__)), "cookies.txt"),
    "--ffmpeg-location", ffmpeg_dir,
    "--js-runtimes", "node",  # YouTube's n-challenge needs a JS runtime + yt-dlp-ejs
    "-f", "bv*[height<=1080]+ba/b",
    "--merge-output-format", "mkv",
    "--write-info-json", "--write-subs", "--write-auto-subs", "--sub-langs", "en.*",
    "--no-playlist", "--restrict-filenames",
    "-o", os.path.join(out, "%(id)s.%(ext)s"),
]))
