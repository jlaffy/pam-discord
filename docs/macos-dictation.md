# Optional: dictate pam prompts on macOS

This is an optional macOS feature, not part of the pam installation. It turns speech into text in
Discord's normal message box. pam receives the resulting text, not the original audio.

## Set it up

1. Open **Apple menu → System Settings → Keyboard**.
2. Scroll to **Dictation** and turn it on.
3. Under **Shortcut**, choose the shortcut you want to use. **Press Fn Key Twice** is a convenient
   choice when available.
4. Select your microphone and language if macOS asks.

## Use it with pam

1. Open a pam project in the Discord desktop app.
2. Click the message box so the text cursor is visible.
3. Press your Dictation shortcut.
4. After the tone or pulsing cursor, speak your prompt.
5. Press the shortcut again, the microphone key, or **Escape** to stop.
6. Review the text, then press **Return** to send it.

You can say commands such as “new line,” “comma,” and “period.” Discord receives ordinary text, so
you can edit it before sending.

If Dictation does not start, confirm that Discord has microphone access under **System Settings →
Privacy & Security → Microphone** and that Voice Control is not enabled. macOS uses Voice Control
instead of standard Dictation when Voice Control is on.

See Apple's current guide: [Dictate messages and documents on
Mac](https://support.apple.com/guide/mac-help/mh40584/mac).
