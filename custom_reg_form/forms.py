from .models import ExtraInfo
from django.conf import settings
from django.forms import ModelForm
from django.utils.translation import ugettext_lazy as _
from openedx.core.djangoapps.site_configuration import helpers as configuration_helpers


class ExtraInfoForm(ModelForm):
    """
    The fields on this form are derived from the ExtraInfo model in models.py.
    """

    def __init__(self, *args, **kwargs):
        super(ExtraInfoForm, self).__init__(*args, **kwargs)
        self.fields["allow_marketing_emails"].label = _(
            u"I agree to get marketing emails from {platform_name}"
        ).format(
            platform_name=configuration_helpers.get_value(
                "PLATFORM_NAME", settings.PLATFORM_NAME
            )
        )

    class Meta(object):
        model = ExtraInfo
        fields = ("allow_marketing_emails",)
