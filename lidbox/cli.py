"""
lidbox command line interface.
"""
import argparse
import itertools
import os
import sys

import lidbox
import lidbox.schemas


def create_argparser():
    """Root argparser for all lidbox command line interface commands"""
    description = lidbox.__doc__ + "For help on other commands, try e.g. lidbox e2e -h."
    root_parser = argparse.ArgumentParser(
        prog=lidbox.__name__,
        description=description,
    )
    subparsers = root_parser.add_subparsers(title="subcommands")
    # Create command line options for all valid commands
    for command_group in VALID_COMMANDS:
        # Add subparser for this command group
        group_argparser = command_group.create_argparser(subparsers)
        group_argparser.set_defaults(cmd_class=command_group)
    return root_parser


class ExpandAbspath(argparse.Action):
    """Simple argparse action to expand path arguments to full paths using os.path.abspath."""
    def __call__(self, parser, namespace, path, *args, **kwargs):
        setattr(namespace, self.dest, os.path.abspath(path))


class Command:
    """
    Base command class for options shared by all lidbox commands.
    """
    # All commands have tasks that can be executed, depending on given arguments
    tasks = ()

    @classmethod
    def create_argparser(cls, subparsers):
        parser = subparsers.add_parser(
            cls.__name__.lower(),
            description=cls.__doc__,
            add_help=False
        )
        required = parser.add_argument_group("required", description="Required arguments")
        required.add_argument("lidbox_config_yaml_path",
            type=str,
            action=ExpandAbspath,
            help="Path to yaml-file, containing the lidbox configuration.")
        optional = parser.add_argument_group(
                "global options",
                description="Optional, global arguments for all subcommands and tasks.")
        optional.add_argument("--run-cProfile",
            action="store_true",
            default=False,
            help="Do profiling on all Python function calls and write results into a file in the working directory.")
        optional.add_argument("--run-tf-profiler",
            action="store_true",
            default=False,
            help="Run the TensorFlow profiler and create a trace event file.")
        optional.add_argument("--help", "-h",
            action="help",
            help="Show this message and exit.")
        optional.add_argument("--verbosity", "-v",
            action="count",
            default=0,
            help="Increases output verbosity.")
        return parser

    def __init__(self, args):
        self.args = args

    def run_tasks(self):
        given_tasks = [
            getattr(self, task_name)
            for task_name in self.__class__.tasks
            if getattr(self.args, task_name) and hasattr(self, task_name)
        ]
        if not given_tasks:
            print("Error: No tasks given, doing nothing", file=sys.stderr)
            return 2
        for task_fn in given_tasks:
            # Users might pipe output of some command to POSIX commands e.g. head, tail, cat etc.
            # These might emit SIGPIPE signals back to tell us there is no need to print more data.
            # In Python this is handled by throwing a BrokenPipeError exception.
            # In that case, let's stop without errors and exit with code 0.
            try:
                ret = task_fn()
            except BrokenPipeError:
                return 0
            if ret:
                return ret

    def run(self):
        args = self.args
        if args.verbosity > 1:
            print("Running lidbox command '{}' with arguments:".format(self.__class__.__name__.lower()))
            lidbox.yaml_pprint(vars(args))
            print()


class E2E(Command):
    """
    Run everything end-to-end as specified in the config file and user script.
    """
    def run(self):
        # These imports are here to avoid importing TensorFlow when the user e.g. requests just the help string from the command
        import lidbox.api
        super().run()
        args = self.args
        if args.verbosity:
            print("Running end-to-end with config file '{}'".format(args.lidbox_config_yaml_path))
        split2meta, labels, config = lidbox.api.load_splits_from_config_file(args.lidbox_config_yaml_path)
        split2ds = lidbox.api.create_datasets(split2meta, labels, config)
        history = lidbox.api.run_training(split2ds, config)
        metrics = lidbox.api.evaluate_test_set(split2ds, split2meta, labels, config)
        lidbox.api.write_metrics(metrics, config)


class Evaluate(Command):
    """
    Evaluate the test set with a trained model.
    """
    def run(self):
        import lidbox.api
        import lidbox.dataset.pipelines
        super().run()
        args = self.args
        if args.verbosity:
            print("Running evaluation with config file '{}'".format(args.lidbox_config_yaml_path))
        split2meta, labels, config = lidbox.api.load_splits_from_config_file(args.lidbox_config_yaml_path)
        test_split_key = config["experiment"]["data"]["test"]["split"]
        # Run the pipeline only for the test split
        split2meta = {split: meta for split, meta in split2meta.items() if split == test_split_key}
        split2ds = lidbox.api.create_datasets(split2meta, labels, config)
        metrics = lidbox.api.evaluate_test_set(split2ds, split2meta, labels, config)
        lidbox.api.write_metrics(metrics, config)


class Utils(Command):
    """
    Simple utilities.
    """
    tasks = (
        "validate_config_file",
    )

    @classmethod
    def create_argparser(cls, subparsers):
        parser = super().create_argparser(subparsers)
        optional = parser.add_argument_group("utils options")
        optional.add_argument("--validate-config-file",
            action="store_true",
            help="Use a JSON schema to check the given config file is valid.")
        return parser

    def validate_config_file(self):
        args = self.args
        errors = lidbox.schemas.validate_config_file_and_get_error_string(args.lidbox_config_yaml_path, args.verbosity)
        if errors:
            print(errors, file=sys.stderr)
            return 1
        elif args.verbosity:
            print("File '{}' ok".format(args.lidbox_config_yaml_path))

    def run(self):
        return self.run_tasks()


class Kaldi(Command):
    """
    TODO
    """

    @classmethod
    def create_argparser(cls, subparsers):
        parser = super().create_argparser(subparsers)
        required = parser.add_argument_group("kaldi arguments")
        required.add_argument("wavscp", type=str, action=ExpandAbspath)
        required.add_argument("durations", type=str, action=ExpandAbspath)
        required.add_argument("utt2lang", type=str, action=ExpandAbspath)
        required.add_argument("utt2seg", type=str, action=ExpandAbspath)
        required.add_argument("task", choices=("uniform-segment",))
        optional = parser.add_argument_group("kaldi options")
        optional.add_argument("--offset", type=float)
        optional.add_argument("--window-len", type=float)
        return parser

    def uniform_segment(self):
        def parse_kaldifile(path):
            with open(path) as f:
                for line in f:
                    yield line.strip().split()
        args = self.args
        if os.path.exists(args.utt2seg):
            print("error: segments file already exists, not overwriting: {}".format(args.utt2seg))
            return 1
        file2lang = dict(parse_kaldifile(args.utt2lang))
        utt2seg = {}
        utt2lang = {}
        for fileid, dur in parse_kaldifile(args.durations):
            dur = float(dur)
            num_frames = int(1 + max(0, (dur - args.window_len + args.offset)/args.offset))
            for utt_num in range(num_frames):
                start = utt_num * args.offset
                end = start + args.window_len
                uttid = "{}_{}".format(fileid, utt_num)
                utt2seg[uttid] = (fileid, start, end if end <= dur else -1)
                utt2lang[uttid] = file2lang[fileid]
        # assume mono left
        channel = 0
        with open(args.utt2seg, "w") as f:
            for uttid, (fileid, start, end) in utt2seg.items():
                print(
                    uttid,
                    fileid,
                    format(float(start), ".2f"),
                    format(float(end), ".2f") if end >= 0 else '-1',
                    channel,
                    file=f
                )
        shutil.copyfile(args.utt2lang, args.utt2lang + ".old")
        with open(args.utt2lang, "w") as f:
            for uttid, lang in utt2lang.items():
                print(uttid, lang, file=f)

    def run(self):
        super().run()
        args = self.args
        if args.task == "uniform-segment":
            if args.verbosity:
                print("Creating a segmentation file for all utterances in wav.scp with fixed length segments")
            assert args.offset
            assert args.window_len
            assert args.offset <= args.window_len
            return self.uniform_segment()
        return 1


VALID_COMMANDS = (
    E2E,
    Evaluate,
    Kaldi,
    Utils,
)
