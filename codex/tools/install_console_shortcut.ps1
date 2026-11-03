param(
    [string]$Python,
    [string]$Destination = [Environment]::GetFolderPath('DesktopDirectory'),
    [string]$Inspect
)
$ErrorActionPreference = 'Stop'
[Console]::OutputEncoding = New-Object Text.UTF8Encoding
# WScript.Shell can round-trip paths through the system ANSI code page. Use
# IShellLinkW and IPersistFile directly so Chinese names also work on en-US Windows.
Add-Type -TypeDefinition @'
using System;
using System.IO;
using System.Text;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;
namespace SnowballConsole {
    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    internal class ShellLink { }
    [ComImport, Guid("000214F9-0000-0000-C000-000000000046"),
     InterfaceType(ComInterfaceType.InterfaceIsIUnknown)]
    internal interface IShellLinkW {
        void GetPath([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path, int count, IntPtr data, uint flags);
        void GetIDList(out IntPtr list);
        void SetIDList(IntPtr list);
        void GetDescription([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder text, int count);
        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string text);
        void GetWorkingDirectory([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path, int count);
        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string path);
        void GetArguments([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder text, int count);
        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string text);
        void GetHotkey(out short key);
        void SetHotkey(short key);
        void GetShowCmd(out int command);
        void SetShowCmd(int command);
        void GetIconLocation([Out, MarshalAs(UnmanagedType.LPWStr)] StringBuilder path, int count, out int index);
        void SetIconLocation([MarshalAs(UnmanagedType.LPWStr)] string path, int index);
        void SetRelativePath([MarshalAs(UnmanagedType.LPWStr)] string path, uint reserved);
        void Resolve(IntPtr window, uint flags);
        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string path);
    }
    public class LinkInfo {
        public string target, arguments, cwd, description;
    }
    public static class Shortcut {
        public const string Marker = "Snowball Companion local console";
        public static LinkInfo Read(string path) {
            object obj = new ShellLink();
            try {
                ((IPersistFile)obj).Load(Path.GetFullPath(path), 0);
                IShellLinkW link = (IShellLinkW)obj;
                StringBuilder target = new StringBuilder(32768), args = new StringBuilder(32768),
                    cwd = new StringBuilder(32768), description = new StringBuilder(1024);
                link.GetPath(target, target.Capacity, IntPtr.Zero, 0);
                link.GetArguments(args, args.Capacity);
                link.GetWorkingDirectory(cwd, cwd.Capacity);
                link.GetDescription(description, description.Capacity);
                return new LinkInfo { target = target.ToString(), arguments = args.ToString(),
                    cwd = cwd.ToString(), description = description.ToString() };
            } finally { Marshal.FinalReleaseComObject(obj); }
        }
        public static bool Write(string path, string target, string args, string cwd) {
            path = Path.GetFullPath(path);
            if (File.Exists(path) && Read(path).description != Marker) return false;
            object obj = new ShellLink();
            try {
                IShellLinkW link = (IShellLinkW)obj;
                link.SetPath(target);
                link.SetArguments(args);
                link.SetWorkingDirectory(cwd);
                link.SetDescription(Marker);
                ((IPersistFile)obj).Save(path, true);
                return true;
            } finally { Marshal.FinalReleaseComObject(obj); }
        }
    }
}
'@
if ($Inspect) {
    [SnowballConsole.Shortcut]::Read($Inspect) | ConvertTo-Json -Compress
    return
}
if (-not $Python) { throw 'Python is required to create the console shortcut.' }
$root = Split-Path $PSScriptRoot
$script = Join-Path $root 'Open-Snowball.ps1'
if (-not (Test-Path -LiteralPath $script) -or -not (Test-Path -LiteralPath $Python)) { throw 'Console launcher or Python is missing.' }
if (-not (Test-Path -LiteralPath $Destination)) { New-Item -ItemType Directory -Path $Destination -Force | Out-Null }
$name = ([char]0x96EA).ToString() + ([char]0x7403).ToString() + ([char]0x63A7).ToString() + ([char]0x5236).ToString() + ([char]0x53F0).ToString() + '.lnk'
$path = Join-Path $Destination $name
$target = Join-Path $env:SystemRoot 'System32/WindowsPowerShell/v1.0/powershell.exe'
$arguments = '-NoProfile -ExecutionPolicy Bypass -File "' + $script + '" -Python "' + $Python + '"'
if (-not [SnowballConsole.Shortcut]::Write($path, $target, $arguments, $root)) {
    Write-Warning 'An unrelated desktop shortcut has the same name. It was preserved; use Open-Snowball.cmd instead.'
    return
}
Write-Output ('Snowball Console shortcut created: ' + $path)
