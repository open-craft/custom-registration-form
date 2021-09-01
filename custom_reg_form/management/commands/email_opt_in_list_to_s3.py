"""
Admin command to export User/UserProfile/Registration Extension fields to csv file on s3.
"""

import logging
import csv
import io
import tempfile
import importlib

import boto3

from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model

from django.db import connections
from django.conf import settings
from django.forms import ModelForm
from botocore.exceptions import NoCredentialsError

from common.djangoapps.student.models import UserProfile
from common.djangoapps.util.query import read_replica_or_default


LOGGER = logging.getLogger(__name__)

def get_registration_ext_model():
    """
    Returns the `REGISTRATION_EXTENSION_MODEL` class.

    First, try to retrieve the `REGISTRATION_EXTENSION_MODEL` class based on
    the Python dotted path set in the settings.

    If the dotted path cannot be imported or not set, as a fallback, if the
    `REGISTRATION_EXTENSION_FORM` is set, the `REGISTRATION_EXTENSION_MODEL`
    is inferred from the form's Meta class. In case we cannot get the model
    class through the Meta class, a `RuntimeError` exception will be raised,
    indicating the runtime failure.
    """

    def get_model_class(model_class_path):
        try:
            module_path = ".".join(model_class_path.split(".")[:-1])
            module = importlib.import_module(module_path)
            return getattr(module, model_class_path.split(".")[-1])
        except AttributeError as exc:
            LOGGER.warning(str(exc))

    if hasattr(settings, "REGISTRATION_EXTENSION_MODEL"):
        return get_model_class(settings.REGISTRATION_EXTENSION_MODEL)

    if hasattr(settings, "REGISTRATION_EXTENSION_FORM"):
        model_class = get_model_class(settings.REGISTRATION_EXTENSION_FORM)
        if issubclass(model_class, ModelForm):
            return model_class.Meta.model

    raise RuntimeError("Couldn't infer the model path to the registration extension model.")

def infer_field_model(field):
    """Returns the model of which `field` is a member"""

    # check if in the User model
    user_model = get_user_model()
    if hasattr(user_model, field):
        return user_model

    # check if in the UserProfile model
    if hasattr(UserProfile, field):
        return UserProfile

    # check if in the REGISTRAION_EXTENSION_MODEL
    reg_ext_model_class = get_registration_ext_model()
    if hasattr(reg_ext_model_class, field):
        return reg_ext_model_class

    raise Exception("Couldn't infer field: `{}` model.".format(field))

# pylint: disable=protected-access
def construct_query(fields):
    """Returns a string `query` to be passed to `cursor.execute`"""

    reg_ext_table = get_registration_ext_model()._meta.db_table
    user_table = get_user_model()._meta.db_table
    field_names = ", ".join([
        ".".join([infer_field_model(field)._meta.db_table, field])
        for field in fields
    ])

    query = """
        SELECT 
            {field_names}
        FROM {user_table} 
            LEFT JOIN {reg_ext_table} ON {reg_ext_table}.user_id = auth_user.id
            JOIN auth_userprofile ON auth_userprofile.user_id = auth_user.id;
        """.format(
            field_names=field_names,
            user_table=user_table,
            reg_ext_table=reg_ext_table
        )

    return query


class Command(BaseCommand):
    """
    Admin command to export User/UserProfile/Registration Extension fields to csv file on s3
    """

    help = """
    Exports users email opt-in preferences to an s3 bucket

    Usage:
    - this script is a general purpose way to export fields, from the auth_user, auth_user_profile,
    and the registration form extension models
    - specifying the registration form extension model
        - the script will try to infer it from the registration form meta field
        - if not applicable the script will try to get it from the
          REGISTRATION_EXTENSION_MODEL setting
    - specifying fields
        - fields can be specified in an adhoc manner using "--fields", in this case fields will
        be determined in this order:
            - the field will be checked against auth_user, if not found then auth_user_profile,
            if not found then the registration extension model, if not found exit with error code 1
    - specifying the output order
        - the order specified in the "--fields" option will be used
    - specifying the aws s3 access
        - using the ENV VARS supported by boto3:
            - AWS_ACCESS_KEY_ID
            - AWS_SECRET_ACCESS_KEY
            - AWS_PROFILE
            - AWS_SHARED_CREDENTIALS_FILE
        - you can specify the aws profile using "--aws-profile", specify which profile to use from
          the ~/.aws/credentials or ~/.aws/config file
        - you can specify which settings to use for aws access key/secret
        like so: "--aws-access-key-setting=CVS_EXPORTER_S3_KEY" and using "--aws-secret-key-setting"
    - other options:
        - "--skip-row-if-field-null", skips the row when the value of one of the fields is null
    """

    def add_arguments(self, parser):
        parser.add_argument(
            "--use-temp-file",
            action="store_true",
            help=(
                "Use a temp file instead of keeping the whole csv in memory,"
                "useful for when expecting a large data set"
            ),
        )
        parser.add_argument(
            "-s", "--skip-row-if-field-null",
            dest="skip_when_null",
            action="store_true",
            help="""
            when enabled the script skips the row when the value of one of the fields is null (default: True)
            """
        )
        parser.add_argument(
            "-ns",
            "--no-skip-row-if-field-null",
            dest="skip_when_null",
            action="store_false"
        )
        parser.add_argument(
            "--aws-access-key-setting",
            type=str,
            help="specify which setting to use as an aws access key",
        )
        parser.add_argument(
            "--aws-secret-key-setting",
            type=str,
            help="specify which setting to use as an aws secret key",
        )
        parser.add_argument(
            "--aws-profile",
            type=str,
            help="specify which aws profile to use"
        )
        parser.add_argument(
            "--fields",
            type=str,
            nargs="+",
            help="fields to include in the output, see docstring header for how to specify fields",
        )
        parser.add_argument(
            "--s3-bucket-name",
            type=str,
            help="aws s3 bucket name where the csv file will be saved.",
        )
        parser.add_argument(
            "--s3-object-name",
            type=str,
            help="the name under which the output will be saved.",
        )
        parser.set_defaults(skip_when_null=True)

    def handle(self, *args, **options):
        """Handle command execution."""

        cursor = connections[read_replica_or_default()].cursor()
        cursor.execute(construct_query(options["fields"]))

        # The csv writer and boto3 client require different file modes
        binary_fd = tempfile.TemporaryFile(mode="w+b") if options["use_temp_file"] else io.BytesIO()
        text_fd = io.TextIOWrapper(binary_fd, encoding="utf-8", write_through=True)
        writer = csv.writer(text_fd)
		# write csv header
        writer.writerow(options["fields"])

        while True:
            # using the default arraysize as described in:
            # https://www.python.org/dev/peps/pep-0249/#fetchmany
            rows = cursor.fetchmany()

            if not rows:
                break

            for row in rows:
                if options["skip_when_null"] and None in row:
                    continue

                writer.writerow(row)

        cursor.close()
        binary_fd.seek(0)

        aws_access_key_id = None
        if options["aws_access_key_setting"]:
            aws_access_key_id = getattr(settings, options["aws_access_key_setting"])

        aws_secret_access_key = None
        if options["aws_secret_key_setting"]:
            aws_secret_access_key = getattr(settings, options["aws_secret_key_setting"])

        session = boto3.Session(
            profile_name=options["aws_profile"],
            aws_access_key_id=aws_access_key_id,
            aws_secret_access_key=aws_secret_access_key,
        )

        s3_client = session.resource("s3")

        try:
            s3_client.Bucket(options["s3_bucket_name"]).put_object(  # pylint: disable=no-member
                Key=options["s3_object_name"],
                Body=binary_fd
            )
        except NoCredentialsError as error:
            LOGGER.exception(error)

        text_fd.close()
        binary_fd.close()
