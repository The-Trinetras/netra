namespace Netra.Desktop.ViewModels;

// Composes the four accessible views the AgentSpec/M5 guide require
// (Library, Study, Conversation, Preferences/status). MainWindow hosts this
// as its DataContext and binds each TabItem's content to the corresponding
// child view model — see Views/MainWindow.xaml.
public sealed class ShellViewModel : ViewModelBase, IDisposable
{
    public ShellViewModel(
        LibraryViewModel libraryViewModel,
        StudyViewModel studyViewModel,
        ConversationViewModel conversationViewModel,
        PreferencesViewModel preferencesViewModel)
    {
        Library = libraryViewModel;
        Study = studyViewModel;
        Conversation = conversationViewModel;
        Preferences = preferencesViewModel;
    }

    public LibraryViewModel Library { get; }
    public StudyViewModel Study { get; }
    public ConversationViewModel Conversation { get; }
    public PreferencesViewModel Preferences { get; }

    public void Dispose() => Conversation.Dispose();
}
