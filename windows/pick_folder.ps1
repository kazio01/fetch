# Folder picker for "Index Samples.cmd": prints the chosen folder (nothing if cancelled).
Add-Type -AssemblyName System.Windows.Forms
$d = New-Object System.Windows.Forms.FolderBrowserDialog
$d.Description = "Fetch: choose a sample folder (local or on the network)"
$d.ShowNewFolderButton = $false
if ($d.ShowDialog() -eq [System.Windows.Forms.DialogResult]::OK) { $d.SelectedPath }
