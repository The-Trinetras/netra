using System.Runtime.ExceptionServices;
using System.Threading;
using System.Windows.Controls;
using System.Windows.Input;
using System.Windows.Threading;
using Netra.Desktop.Library;
using Netra.Desktop.Networking;
using Netra.Desktop.Protocol.Dto;
using Netra.Desktop.State;
using Netra.Desktop.ViewModels;
using Netra.Desktop.Views;
using Xunit;

namespace Netra.Desktop.Tests;

// Loads the real LibraryView (compiled XAML) on an STA thread and checks what
// the keyboard paths actually hand to the commands. Moving through a ListBox
// with the arrow keys changes ListBox.SelectedItem, which is what these tests
// set. This is binding evidence, not a screen-reader test: NVDA announcement
// still needs a manual Windows/NVDA check.
public sealed class LibraryViewBindingTests
{
    [Fact]
    public void EnterOrTheButtonOnAKeyboardSelectedLectureResult_SelectsThatResult()
    {
        RunOnSta(() =>
        {
            var viewModel = new LibraryViewModel(new FixtureSourcePreparationService(), new FixtureVideoDiscoveryService())
            {
                VideoSearchQuery = "ohm's law lecture",
            };
            viewModel.SearchVideosAsync().GetAwaiter().GetResult();
            var view = new LibraryView { DataContext = viewModel };
            using var host = Host.Show(view);
            var list = (ListBox)view.FindName("VideoResultsList");
            var button = (Button)view.FindName("SelectVideoResultButton");

            list.SelectedIndex = 1;
            Pump();

            var enter = Assert.IsType<KeyBinding>(Assert.Single(list.InputBindings));
            Assert.Same(viewModel.VideoResults[1], enter.CommandParameter);
            Assert.Same(viewModel.VideoResults[1], button.CommandParameter);

            enter.Command.Execute(enter.CommandParameter);
            Assert.Same(viewModel.VideoResults[1], viewModel.SelectedVideoResult);
        });
    }

    [Fact]
    public void ServerSources_AreNamedForScreenReadersAndOpenTheKeyboardSelectedItem()
    {
        RunOnSta(() =>
        {
            var state = new ClientSessionState();
            state.Initialize(Guid.NewGuid(), 0);
            var catalog = new RecordingCatalog();
            var viewModel = new LibraryViewModel(
                new FixtureSourcePreparationService(), new FixtureVideoDiscoveryService(),
                new LibraryServerAccess(catalog, state));
            viewModel.RefreshSourcesAsync(CancellationToken.None).GetAwaiter().GetResult();
            var view = new LibraryView { DataContext = viewModel };
            using var host = Host.Show(view);
            var list = (ListBox)view.FindName("ServerSourcesList");
            var button = (Button)view.FindName("OpenServerSourceButton");

            list.SelectedIndex = 1;
            Pump();

            Assert.Same(catalog.Items[1], viewModel.SelectedAvailableSource);
            Assert.Same(catalog.Items[1], button.CommandParameter);
            var enter = Assert.IsType<KeyBinding>(Assert.Single(list.InputBindings));
            Assert.Same(catalog.Items[1], enter.CommandParameter);
            Assert.Equal("Your sources", System.Windows.Automation.AutomationProperties.GetName(list));

            button.Command.Execute(button.CommandParameter);
            Pump();
            Assert.Equal(new[] { catalog.Items[1] }, catalog.Opened);
        });
    }

    [Fact]
    public void WithoutAServer_TheServerSourcesSectionIsNotShown()
    {
        RunOnSta(() =>
        {
            var viewModel = new LibraryViewModel(new FixtureSourcePreparationService(), new FixtureVideoDiscoveryService());
            var view = new LibraryView { DataContext = viewModel };
            using var host = Host.Show(view);
            var list = (ListBox)view.FindName("ServerSourcesList");
            Pump();

            var section = (System.Windows.FrameworkElement)list.Parent;
            Assert.Equal(System.Windows.Visibility.Collapsed, section.Visibility);
        });
    }

    // Shown off-screen and never activated: bindings, name scopes and input
    // bindings behave as in the running app (loaded, in a PresentationSource).
    private sealed class Host : IDisposable
    {
        private readonly System.Windows.Window _window;

        private Host(System.Windows.Window window) => _window = window;

        public static Host Show(System.Windows.FrameworkElement content)
        {
            var window = new System.Windows.Window
            {
                Content = content,
                Width = 600,
                Height = 800,
                Left = -20000,
                Top = -20000,
                WindowStartupLocation = System.Windows.WindowStartupLocation.Manual,
                ShowInTaskbar = false,
                ShowActivated = false,
                WindowStyle = System.Windows.WindowStyle.None,
            };
            window.Show();
            Pump();
            return new Host(window);
        }

        public void Dispose() => _window.Close();
    }

    private static void Pump() =>
        Dispatcher.CurrentDispatcher.Invoke(DispatcherPriority.ApplicationIdle, new Action(() => { }));

    private static void RunOnSta(Action test)
    {
        Exception? failure = null;
        var thread = new Thread(() =>
        {
            try
            {
                test();
            }
            catch (Exception ex)
            {
                failure = ex;
            }
            finally
            {
                Dispatcher.CurrentDispatcher.InvokeShutdown();
            }
        });
        thread.SetApartmentState(ApartmentState.STA);
        thread.Start();
        thread.Join();
        if (failure is not null)
        {
            ExceptionDispatchInfo.Capture(failure).Throw();
        }
    }

    private sealed class RecordingCatalog : ISourceCatalog
    {
        public List<CatalogSource> Items { get; } = new()
        {
            new("s-1", "Ohm's law chapter", "v-1", 1),
            new("s-2", "Kirchhoff notes", "v-2", 3),
        };

        public List<CatalogSource> Opened { get; } = new();

        public Task<IReadOnlyList<CatalogSource>> ListAsync(CancellationToken cancellationToken) =>
            Task.FromResult<IReadOnlyList<CatalogSource>>(Items);

        public Task<SessionSnapshotPayload> OpenAsync(CatalogSource source, CancellationToken cancellationToken)
        {
            Opened.Add(source);
            return Task.FromResult(new SessionSnapshotPayload
            {
                SessionVersion = 1,
                InteractionMode = SessionInteractionMode.Reading,
                ActiveSourceVersionId = source.ActiveSourceVersionId,
            });
        }
    }
}
