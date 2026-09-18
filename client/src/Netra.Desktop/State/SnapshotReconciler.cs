using Netra.Desktop.Protocol.Dto;

namespace Netra.Desktop.State;

// Applies an authoritative session.snapshot (from the WebSocket or from an
// HTTP session/source response) to local state. Callers run this on the UI
// thread when ClientSessionState is bound to views.
public static class SnapshotReconciler
{
    // The server version is taken as-is: a snapshot pushed on connect/resume
    // is reconciliation, not a client mutation, so it may keep the counter or
    // even lower it (e.g. the very first snapshot after Initialize()).
    public static void Apply(ClientSessionState state, SessionSnapshotPayload snapshot, Guid? sessionId = null)
    {
        state.Initialize(sessionId ?? state.SessionId, snapshot.SessionVersion);
        state.InteractionMode = snapshot.InteractionMode;
        state.ActiveSourceVersionId = snapshot.ActiveSourceVersionId;
        state.CurrentBlockId = snapshot.CurrentBlockId;
        state.CurrentSentenceId = snapshot.CurrentSentenceId;
        state.LastAcknowledgedSentenceId = snapshot.LastAcknowledgedSentenceId;
        state.ActiveTutorLessonId = snapshot.ActiveLesson?.LessonId.ToString();
        state.PendingQuestionId = snapshot.PendingQuestion?.QuestionId;
        state.LastResultSetId = snapshot.LastResultSet?.ResultSetId;
    }

    // For a snapshot returned as the RESULT of one request (HTTP source pin).
    // A replayed request returns the snapshot recorded when it first
    // committed; if the session has moved on since, applying it would roll
    // local state back, so an older snapshot is ignored.
    public static bool ApplyUnlessOlder(ClientSessionState state, SessionSnapshotPayload snapshot)
    {
        if (snapshot.SessionVersion < state.SessionVersion)
        {
            return false;
        }

        Apply(state, snapshot);
        return true;
    }
}
