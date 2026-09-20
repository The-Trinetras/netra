using System.IO;
using System.Threading;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.Wpf;

namespace Netra.Desktop.Video;

// Hosts Netra's player page in WebView2 (approved M5-VIDEO, pinned in F1)
// and locks it down: only the bundled page at the top level, only YouTube
// embeds in frames, no new windows, downloads, devtools, context menus,
// browser shortcuts, script dialogs or permissions (the page can never get
// the microphone or camera). Messages are accepted only from the page itself.
public sealed class WebView2PlayerSurface : IPlayerSurface
{
    private readonly WebView2 _view;

    public WebView2PlayerSurface(WebView2 view)
    {
        _view = view;
    }

    public event EventHandler<string>? MessageReceived;

    // UI thread.
    public async Task InitializeAsync(CancellationToken cancellationToken)
    {
        CoreWebView2 core;
        try
        {
            // The per-user folder, not next to the program, which may not be writable.
            var userData = Path.Combine(
                Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Netra", "WebView2");
            // Chromium's default autoplay policy requires a user gesture inside
            // the document. The student's key press happens in WPF, so
            // playVideo() from the player page counts as programmatic autoplay
            // and is refused: the video stays on its poster and "Play (K)" does
            // nothing. Keyboard control of the lecture is required
            // (.claude/rules/client.md), and the player sets disablekb so
            // focusing the frame is not an alternative, so the gesture
            // requirement is lifted for this WebView only. It applies to
            // Netra's own browser environment, never to the system browser,
            // and the page can still only reach the YouTube embed the CSP
            // allows.
            var options = new CoreWebView2EnvironmentOptions
            {
                AdditionalBrowserArguments = "--autoplay-policy=no-user-gesture-required",
            };
            var environment = await CoreWebView2Environment.CreateAsync(
                browserExecutableFolder: null, userDataFolder: userData, options: options);
            await _view.EnsureCoreWebView2Async(environment);
            core = _view.CoreWebView2;
        }
        catch (WebView2RuntimeNotFoundException)
        {
            throw new PlayerUnavailableException(
                "The lecture player needs the Microsoft Edge WebView2 Runtime, which is not installed on this computer.");
        }

        cancellationToken.ThrowIfCancellationRequested();

        var settings = core.Settings;
        settings.AreDevToolsEnabled = false;
        settings.AreDefaultContextMenusEnabled = false;
        settings.AreBrowserAcceleratorKeysEnabled = false;
        settings.AreDefaultScriptDialogsEnabled = false;
        settings.AreHostObjectsAllowed = false;
        settings.IsStatusBarEnabled = false;
        settings.IsGeneralAutofillEnabled = false;
        settings.IsPasswordAutosaveEnabled = false;
        settings.IsSwipeNavigationEnabled = false;
        settings.IsWebMessageEnabled = true;

        var playerFolder = Path.Combine(AppContext.BaseDirectory, "Video", "Player");
        core.SetVirtualHostNameToFolderMapping(
            PlayerNavigationPolicy.PlayerHost, playerFolder, CoreWebView2HostResourceAccessKind.DenyCors);

        core.NavigationStarting += (_, e) => e.Cancel = !PlayerNavigationPolicy.IsAllowedTopLevel(e.Uri);
        core.FrameNavigationStarting += (_, e) => e.Cancel = !PlayerNavigationPolicy.IsAllowedFrame(e.Uri);
        core.NewWindowRequested += (_, e) => e.Handled = true;
        core.DownloadStarting += (_, e) =>
        {
            e.Cancel = true;
            e.Handled = true;
        };
        core.PermissionRequested += (_, e) =>
        {
            e.State = CoreWebView2PermissionState.Deny;
            e.Handled = true;
        };
        core.WebMessageReceived += (_, e) =>
        {
            if (PlayerNavigationPolicy.IsTrustedMessageSource(e.Source))
            {
                MessageReceived?.Invoke(this, e.WebMessageAsJson);
            }
        };
        core.ProcessFailed += (_, _) => MessageReceived?.Invoke(this, """{"type":"error","code":-1}""");

        var opened = new TaskCompletionSource<bool>(TaskCreationOptions.RunContinuationsAsynchronously);
        void OnCompleted(object? sender, CoreWebView2NavigationCompletedEventArgs e) => opened.TrySetResult(e.IsSuccess);
        core.NavigationCompleted += OnCompleted;
        try
        {
            core.Navigate(PlayerNavigationPolicy.PlayerPage.AbsoluteUri);
            if (!await opened.Task.WaitAsync(cancellationToken))
            {
                throw new PlayerUnavailableException("The lecture player page could not be opened.");
            }
        }
        finally
        {
            core.NavigationCompleted -= OnCompleted;
        }
    }

    public void Post(string json) => _view.CoreWebView2?.PostWebMessageAsJson(json);
}
