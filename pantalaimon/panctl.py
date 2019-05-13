"""Cli utility to control pantalaimon."""

import attr
import asyncio
import argparse
import sys

from typing import List
from itertools import zip_longest

from prompt_toolkit import PromptSession
from prompt_toolkit.eventloop.defaults import use_asyncio_event_loop
from prompt_toolkit.patch_stdout import patch_stdout
from prompt_toolkit.completion import Completer, Completion, PathCompleter
from prompt_toolkit.document import Document
from prompt_toolkit import print_formatted_text, HTML

from pydbus import SessionBus
from gi.repository import GLib

use_asyncio_event_loop()


class ParseError(Exception):
    pass


class PanctlArgParse(argparse.ArgumentParser):
    def print_usage(self, file=None):
        pass

    def error(self, message):
        message = (
            f"Error: {message} "
            f"(see help)"
        )
        print(message)
        raise ParseError


class PanctlParser():
    def __init__(self):
        self.parser = PanctlArgParse()
        subparsers = self.parser.add_subparsers(dest="subcommand")
        subparsers.add_parser("list-users")

        list_devices = subparsers.add_parser("list-devices")
        list_devices.add_argument("pan_user", type=str)
        list_devices.add_argument("user_id", type=str)

        start = subparsers.add_parser("start-verification")
        start.add_argument("pan_user", type=str)
        start.add_argument("user_id", type=str)
        start.add_argument("device_id", type=str)

        accept = subparsers.add_parser("accept-verification")
        accept.add_argument("pan_user", type=str)
        accept.add_argument("user_id", type=str)
        accept.add_argument("device_id", type=str)

        confirm = subparsers.add_parser("confirm-verification")
        confirm.add_argument("pan_user", type=str)
        confirm.add_argument("user_id", type=str)
        confirm.add_argument("device_id", type=str)

        verify = subparsers.add_parser("verify-device")
        verify.add_argument("pan_user", type=str)
        verify.add_argument("user_id", type=str)
        verify.add_argument("device_id", type=str)

        unverify = subparsers.add_parser("verify-device")
        unverify.add_argument("pan_user", type=str)
        unverify.add_argument("user_id", type=str)
        unverify.add_argument("device_id", type=str)

        import_keys = subparsers.add_parser("import-keys")
        import_keys.add_argument("pan_user", type=str)
        import_keys.add_argument("path", type=str)
        import_keys.add_argument("passphrase", type=str)

        export_keys = subparsers.add_parser("export-keys")
        export_keys.add_argument("pan_user", type=str)
        export_keys.add_argument("path", type=str)
        export_keys.add_argument("passphrase", type=str)

    def parse_args(self, argv):
        return self.parser.parse_args(argv)


@attr.s
class PanCompleter(Completer):
    """Completer for panctl commands."""

    commands = attr.ib(type=List[str])
    ctl = attr.ib()
    devices = attr.ib()
    path_completer = PathCompleter(expanduser=True)

    def complete_commands(self, last_word):
        """Complete the available commands."""
        compl_words = self.filter_words(self.commands, last_word)
        for compl_word in compl_words:
            yield Completion(compl_word, -len(last_word))

    def complete_users(self, last_word, pan_user):
        devices = self.devices.List(pan_user)
        users = set(device["user_id"] for device in devices)
        compl_words = self.filter_words(users, last_word)

        for compl_word in compl_words:
            yield Completion(compl_word, -len(last_word))

        return ""

    def complete_devices(self, last_word, pan_user, user_id):
        devices = self.devices.ListUserDevices(pan_user, user_id)
        device_ids = [device["device_id"] for device in devices]
        compl_words = self.filter_words(device_ids, last_word)

        for compl_word in compl_words:
            yield Completion(compl_word, -len(last_word))

        return ""

    def filter_words(self, words, last_word):
        compl_words = []

        for word in words:
            if last_word in word:
                compl_words.append(word)

        return compl_words

    def complete_pan_users(self, last_word):
            users = self.ctl.ListUsers()
            compl_words = self.filter_words([i[0] for i in users], last_word)

            for compl_word in compl_words:
                yield Completion(compl_word, -len(last_word))

    def complete_verification(self, command, last_word, words):
        if len(words) == 2:
            return self.complete_pan_users(last_word)
        elif len(words) == 3:
            pan_user = words[1]
            return self.complete_users(last_word, pan_user)
        elif len(words) == 4:
            pan_user = words[1]
            user_id = words[2]
            return self.complete_devices(last_word, pan_user, user_id)

        return ""

    def complete_key_file_cmds(
        self,
        document,
        complete_event,
        command,
        last_word,
        words
    ):
        if len(words) == 2:
            return self.complete_pan_users(last_word)
        elif len(words) == 3:
            return self.path_completer.get_completions(
                Document(last_word),
                complete_event
            )

        return ""

    def complete_list_devices(self, last_word, words):
        if len(words) == 2:
            return self.complete_pan_users(last_word)
        elif len(words) == 3:
            pan_user = words[1]
            return self.complete_users(last_word, pan_user)

        return ""

    def get_completions(self, document, complete_event):
        """Build the completions."""
        text_before_cursor = document.text_before_cursor
        text_before_cursor = str(text_before_cursor)
        words = text_before_cursor.split(" ")

        last_word = words[-1]

        if len(words) == 1:
            return self.complete_commands(last_word)

        if len(words) > 1:
            command = words[0]

            if command in [
                "start-verification",
                "accept-verification",
                "confirm-verification",
                "cancel-verification",
                "verify-device",
                "unverify-device",
            ]:
                return self.complete_verification(command, last_word, words)

            elif command in [
                "export-keys",
                "import-keys",
            ]:
                return self.complete_key_file_cmds(
                    document,
                    complete_event,
                    command,
                    last_word,
                    words
                )

            elif command == "list-devices":
                return self.complete_list_devices(last_word, words)

        return ""


def grouper(iterable, n, fillvalue=None):
    "Collect data into fixed-length chunks or blocks"
    # grouper('ABCDEFG', 3, 'x') --> ABC DEF Gxx"
    args = [iter(iterable)] * n
    return zip_longest(*args, fillvalue=fillvalue)


def partition_key(key):
    groups = grouper(key, 4, " ")
    return ' '.join(''.join(g) for g in groups)


def get_color(string):
    def djb2(string):
        hash = 5381
        for x in string:
            hash = ((hash << 5) + hash) + ord(x)
        return hash & 0xFFFFFFFF

    colors = [
        "ansiblue",
        "ansigreen",
        "ansired",
        "ansiyellow",
        "ansicyan",
        "ansimagenta",
    ]

    return colors[djb2(string) % 5]


@attr.s
class PanCtl:
    bus = attr.ib(init=False)
    ctl = attr.ib(init=False)
    devices = attr.ib(init=False)

    commands = [
        "list-users",
        "list-devices",
        "export-keys",
        "import-keys",
        "verify-device",
        "unverify-device",
        "start-verification",
        "accept-verification",
        "confirm-verification"
    ]

    def __attrs_post_init__(self):
        self.bus = SessionBus()
        self.pan_bus = self.bus.get("org.pantalaimon1")

        self.ctl = self.pan_bus["org.pantalaimon1.control"]
        self.devices = self.pan_bus["org.pantalaimon1.devices"]

        self.own_message_ids = []

        self.ctl.Response.connect(self.show_response)
        self.devices.VerificationInvite.connect(self.show_sas_invite)
        self.devices.VerificationString.connect(self.show_sas)
        self.devices.VerificationDone.connect(self.sas_done)

    def show_response(self, response_id, pan_user, message):
        if response_id not in self.own_message_ids:
            return

        self.own_message_ids.remove(response_id)

        print(message["message"])

    def sas_done(self, pan_user, user_id, device_id, _):
        print(f"Device {device_id} of user {user_id}"
              f" succesfully verified for pan user {pan_user}.")

    def show_sas_invite(self, pan_user, user_id, device_id, _):
        print(f"{user_id} has started an interactive device "
              f"verification for his device {device_id} with pan user "
              f"{pan_user}\n"
              f"Accept the invitation with the accept-verification command.")

    # The emoji printing logic was taken from weechat-matrix and was written by
    # dkasak.
    def show_sas(self, pan_user, user_id, device_id, _, emoji):
        emojis = [x[0] for x in emoji]
        descriptions = [x[1] for x in emoji]

        centered_width = 12

        def center_emoji(emoji, width):
            # Assume each emoji has width 2
            emoji_width = 2

            # These are emojis that need VARIATION-SELECTOR-16 (U+FE0F) so
            # that they are rendered with coloured glyphs. For these, we
            # need to add an extra space after them so that they are
            # rendered properly in weechat.
            variation_selector_emojis = [
                '☁️',
                '❤️',
                '☂️',
                '✏️',
                '✂️',
                '☎️',
                '✈️'
            ]

            if emoji in variation_selector_emojis:
                emoji += " "

            # This is a trick to account for the fact that emojis are wider
            # than other monospace characters.
            placeholder = '.' * emoji_width

            return placeholder.center(width).replace(placeholder, emoji)

        emoji_str = u"".join(center_emoji(e, centered_width)
                             for e in emojis)
        desc = u"".join(d.center(centered_width) for d in descriptions)
        short_string = u"\n".join([emoji_str, desc])

        print(f"Short authentication string for pan "
              f"user {pan_user} from {user_id} via "
              f"{device_id}:\n{short_string}")

    def list_users(self):
        """List the daemons users."""
        users = self.ctl.ListUsers()
        print("pantalaimon users:")
        for user, device in users:
            print(" ", user, device)

    def list_devices(self, args):
        devices = self.devices.ListUserDevices(args.pan_user, args.user_id)

        print_formatted_text(
            HTML(f"Devices for user <b>{args.user_id}</b>:")
        )

        for device in devices:
            key = partition_key(device["ed25519"])
            color = get_color(device["device_id"])
            print_formatted_text(HTML(
                f" - Device id:    "
                f"<{color}>{device['device_id']}</{color}>\n"
                f"   - Device key: "
                f"<ansiyellow>{key}</ansiyellow>"
            ))

    async def loop(self):
        """Event loop for panctl."""
        completer = PanCompleter(self.commands, self.ctl, self.devices)
        promptsession = PromptSession("panctl> ", completer=completer)

        while True:
            with patch_stdout():
                try:
                    result = await promptsession.prompt(async_=True)
                except EOFError:
                    break

            if not result:
                continue

            parser = PanctlParser()

            try:
                args = parser.parse_args(result.split())
            except ParseError:
                continue

            command = args.subcommand

            if command == "list-users":
                self.list_users()

            elif command == "import-keys":
                self.own_message_ids.append(
                    self.ctl.ImportKeys(
                        args.pan_user,
                        args.path,
                        args.passphrase
                    ))

            elif command == "export-keys":
                self.own_message_ids.append(
                    self.ctl.ExportKeys(
                        args.pan_user,
                        args.path,
                        args.passphrase
                    ))

            elif command == "list-devices":
                self.list_devices(args)

            elif command == "accept-verification":
                self.own_message_ids.append(
                    self.devices.AcceptKeyVerification(
                        args.pan_user,
                        args.user_id,
                        args.device_id
                    ))

            elif command == "confirm-verification":
                self.own_message_ids.append(
                    self.devices.ConfirmKeyVerification(
                        args.pan_user,
                        args.user_id,
                        args.device_id
                    ))


def main():
    loop = asyncio.get_event_loop()
    glib_loop = GLib.MainLoop()

    try:
        panctl = PanCtl()
    except GLib.Error as e:
        print(f"Error, {e}")
        sys.exit(-1)

    fut = loop.run_in_executor(
        None,
        glib_loop.run
    )

    try:
        loop.run_until_complete(panctl.loop())
    except KeyboardInterrupt:
        pass

    GLib.idle_add(glib_loop.quit)
    loop.run_until_complete(fut)


if __name__ == '__main__':
    main()
