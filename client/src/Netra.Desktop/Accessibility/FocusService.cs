using System.Windows;
using System.Windows.Input;

namespace Netra.Desktop.Accessibility;

// Programmatic focus management. The core reading flow must never require
// sighted interaction, so focus moves are always driven by keyboard/command
// actions, never by pointer-only affordances.
public interface IFocusService
{
    void MoveFocusTo(UIElement element);
    void MoveFocusToFirstFocusable(UIElement container);
}

public sealed class FocusService : IFocusService
{
    public void MoveFocusTo(UIElement element)
    {
        if (!element.Focusable)
        {
            element.Focusable = true;
        }

        element.Focus();
        Keyboard.Focus(element);
    }

    public void MoveFocusToFirstFocusable(UIElement container) =>
        container.MoveFocus(new TraversalRequest(FocusNavigationDirection.First));
}
