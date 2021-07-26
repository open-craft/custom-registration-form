from django.conf import settings
from django.db import models

# Backwards compatible settings.AUTH_USER_MODEL
USER_MODEL = getattr(settings, "AUTH_USER_MODEL", "auth.User")


class ExtraInfo(models.Model):
    """
    This model contains two extra fields that will be saved when a user registers.
    The form that wraps this model is in the forms.py file.
    """

    user = models.OneToOneField(USER_MODEL, null=True, on_delete=models.DO_NOTHING)

    allow_marketing_emails = models.BooleanField(
        default=False,
    )

    def __unicode__(self):
        return u"ExtraInfo for user {}".format(self.user.username)
