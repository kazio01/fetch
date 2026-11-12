Fetch for Windows
=================

Semantic search for your sample library, in a VST3 plugin. This folder is either the portable
package (unzipped anywhere) or what the installer put in %LOCALAPPDATA%\Programs\Fetch. The
installer runs step 1 for you and adds Start menu entries (Index a sample folder, Repair, Uninstall).
Everything Fetch installs and writes stays in this folder, except:
  - Documents\Fetch Collections   liked samples and rendered (tempo/key-matched) audio
  - %APPDATA%\Fetch               plugin settings and server-command.txt
  - the VST3 plugin, if you let Setup copy it to C:\Program Files\Common Files\VST3

1. Setup.cmd
   Downloads Python 3.12, PyTorch, the other packages and the CLAP model (~0.8 GB) into this
   folder. Needs an internet connection once. With an NVIDIA graphics card (driver 580 or newer)
   it installs the CUDA build of PyTorch (~4 GB in all) so indexing runs on the GPU, plus the
   CPU build for the search service (it needs no GPU and uses ~0.5 GB less memory); otherwise
   the CPU build (~1 GB). Setup.cmd -Cpu forces the CPU build. Asks whether to install the plugin
   into the standard VST3 folder (administrator rights); otherwise add this package's VST3 folder
   to your DAW's plugin locations.

2. Index Samples.cmd
   Pick your sample folder, or drop folders onto the file. Network folders work: a mapped drive
   (Z:\Samples) or a share path (\\nas\Samples), but a folder on this computer's own disk is
   much faster. Step 1 (CLAP embeddings) runs on the GPU if Setup found one; step 2 (sound
   features, tempo and key) runs on all CPU cores but two (set SAMPLEHAKU_JOBS to change).
   You can stop with Ctrl+C at any time; running it again continues and later only processes new
   or changed files. The result is data\index\index.db.

3. Open your DAW and load the "Fetch" instrument. The plugin starts the search service by itself
   (no window). It finds samples indexed so far; new ones appear within a minute.

Troubleshooting
   - "Fetch Service (console).cmd" runs the service in a window so you can see errors.
     When the plugin starts it, the log goes to data\service.log.
   - The service listens on 127.0.0.1:47811 only (not on the network).
   - Moved this folder? Run Setup.cmd again (it updates %APPDATA%\Fetch\server-command.txt).

Removing
   Installed: Start menu > Fetch > Uninstall Fetch (asks whether to keep your index).
   Portable: delete this folder, %APPDATA%\Fetch and C:\Program Files\Common Files\VST3\Fetch.vst3.
   Documents\Fetch Collections holds your likes and renders: keep it or delete it.
