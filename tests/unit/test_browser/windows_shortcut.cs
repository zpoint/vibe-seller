// A test-only shortcut writer/reader on IShellLinkW (what installers and
// Explorer use), compiled by PowerShell's Add-Type in
// test_win_app_locator_windows.py. WScript.Shell cannot write these
// shortcuts: it is bound to the system ANSI code page and refuses a
// Chinese path on an English Windows.
//
// ReadTarget is the shell's own answer to "what does this .lnk point
// at", used to cross-check the product's .lnk reader (lnk_file.py).

using System;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.ComTypes;

public static class VibetestShortcut
{
    [ComImport, Guid("00021401-0000-0000-C000-000000000046")]
    private class CShellLink
    {
    }

    // Methods in vtable order -- the order IS the ABI.
    [ComImport, InterfaceType(ComInterfaceType.InterfaceIsIUnknown),
     Guid("000214F9-0000-0000-C000-000000000046")]
    private interface IShellLinkW
    {
        void GetPath(
            [Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszFile,
            int cchMaxPath,
            IntPtr pfd,
            uint fFlags);

        void GetIDList(out IntPtr ppidl);

        void SetIDList(IntPtr pidl);

        void GetDescription(
            [Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszName,
            int cchMaxName);

        void SetDescription([MarshalAs(UnmanagedType.LPWStr)] string pszName);

        void GetWorkingDirectory(
            [Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszDir,
            int cchMaxPath);

        void SetWorkingDirectory([MarshalAs(UnmanagedType.LPWStr)] string pszDir);

        void GetArguments(
            [Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszArgs,
            int cchMaxPath);

        void SetArguments([MarshalAs(UnmanagedType.LPWStr)] string pszArgs);

        void GetHotKey(out short pwHotkey);

        void SetHotKey(short wHotkey);

        void GetShowCmd(out int piShowCmd);

        void SetShowCmd(int iShowCmd);

        void GetIconLocation(
            [Out, MarshalAs(UnmanagedType.LPWStr)] System.Text.StringBuilder pszIconPath,
            int cchIconPath,
            out int piIcon);

        void SetIconLocation(
            [MarshalAs(UnmanagedType.LPWStr)] string pszIconPath,
            int iIcon);

        void SetRelativePath(
            [MarshalAs(UnmanagedType.LPWStr)] string pszPathRel,
            uint dwReserved);

        void Resolve(IntPtr hwnd, uint fFlags);

        void SetPath([MarshalAs(UnmanagedType.LPWStr)] string pszFile);
    }

    public static void Create(string linkPath, string targetPath)
    {
        // Each step says its own name when it fails: a bare "E_FAIL has
        // been returned from a call to a COM component" does not tell
        // whether the target or the link path was the problem.
        IShellLinkW link = (IShellLinkW)new CShellLink();
        try
        {
            link.SetPath(targetPath);
        }
        catch (COMException e)
        {
            throw new InvalidOperationException(
                "IShellLink.SetPath failed for the target " + targetPath
                + ": " + e.Message, e);
        }

        try
        {
            ((IPersistFile)link).Save(linkPath, false);
        }
        catch (COMException e)
        {
            throw new InvalidOperationException(
                "IPersistFile.Save failed for the link " + linkPath
                + ": " + e.Message, e);
        }
    }

    // SLGP_RAWPATH: the path as stored, unexpanded and unresolved.
    private const uint SLGP_RAWPATH = 4;

    // STGM_READ
    private const int STGM_READ = 2;

    public static string ReadTarget(string linkPath)
    {
        IShellLinkW link = (IShellLinkW)new CShellLink();
        ((IPersistFile)link).Load(linkPath, STGM_READ);
        System.Text.StringBuilder path = new System.Text.StringBuilder(1024);
        link.GetPath(path, path.Capacity, IntPtr.Zero, SLGP_RAWPATH);
        return path.ToString();
    }
}
