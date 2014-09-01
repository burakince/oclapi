from urlparse import urljoin
from xmlrpclib import DateTime
from django.conf import settings
from django.contrib.auth.models import User
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.core.urlresolvers import reverse
from django.db import models
from django.db.models import Q
from django.db.models.signals import post_save
from django.dispatch import receiver
from djangotoolbox.fields import ListField, EmbeddedModelField
from uuidfield import UUIDField
from concepts.mixins import DictionaryItemMixin
from oclapi.models import SubResourceBaseModel, ResourceVersionModel, VERSION_TYPE
from oclapi.utils import reverse_resource, reverse_resource_version
from sources.models import SourceVersion, Source


class LocalizedText(models.Model):
    uuid = UUIDField(auto=True)
    name = models.TextField()
    type = models.TextField(null=True, blank=True)
    locale = models.TextField()
    locale_preferred = models.BooleanField(default=False)

    def clone(self):
        return LocalizedText(
            uuid=self.uuid,
            name=self.name,
            type=self.type,
            locale=self.locale,
            locale_preferred=self.locale_preferred
        )


CONCEPT_TYPE = 'Concept'


class Concept(SubResourceBaseModel, DictionaryItemMixin):
    concept_class = models.TextField()
    datatype = models.TextField(null=True, blank=True)
    names = ListField(EmbeddedModelField(LocalizedText))
    descriptions = ListField(EmbeddedModelField(LocalizedText))
    retired = models.BooleanField(default=False)

    @property
    def display_name(self):
        return self.get_display_name_for(self)

    @property
    def display_locale(self):
        return self.get_display_locale_for(self)

    @property
    def owner_name(self):
        return self.parent.parent_resource

    @property
    def owner_type(self):
        return self.parent.parent_resource_type

    @property
    def owner_url(self):
        return self.parent.parent_url

    @property
    def num_versions(self):
        return ConceptVersion.objects.filter(versioned_object_id=self.id).count()

    @property
    def names_for_default_locale(self):
        names = []
        for name in self.names:
            if settings.DEFAULT_LOCALE == name.locale:
                names.append(name.name)
        return names

    @property
    def descriptions_for_default_locale(self):
        descriptions = []
        for desc in self.descriptions:
            if settings.DEFAULT_LOCALE == desc.locale:
                descriptions.append(desc.name)
        return descriptions

    @property
    def num_stars(self):
        return 0

    @classmethod
    def resource_type(cls):
        return CONCEPT_TYPE

    @classmethod
    def create_initial_version(cls, obj, **kwargs):
        initial_version = ConceptVersion.for_concept(obj, '_TEMP')
        initial_version.save()
        initial_version.mnemonic = initial_version.id
        initial_version.root_version = initial_version
        initial_version.released = True
        initial_version.save()
        return initial_version

    @classmethod
    def retire(cls, concept):
        if concept.retired:
            return False
        concept.retired = True
        latest_version = ConceptVersion.get_latest_version_of(concept)
        retired_version = latest_version.clone()
        retired_version.retired = True
        latest_source_version = SourceVersion.get_latest_version_of(concept.parent)
        latest_source_version_concepts = latest_source_version.concepts
        retired = False
        try:
            concept.save()

            retired_version.save()
            retired_version.mnemonic = retired_version.id
            retired_version.save()

            latest_source_version.update_concept_version(retired_version)
            latest_source_version.save()
            retired = True
        finally:
            if not retired:
                latest_source_version.concepts = latest_source_version_concepts
                latest_source_version.save()
                retired_version.delete()
                concept.retired = False
                concept.save()
        return retired

    @classmethod
    def count_for_source(cls, src, is_active=True, retired=False):
        return cls.objects.filter(parent_id=src.id, is_active=is_active, retired=retired)

    @staticmethod
    def get_url_kwarg():
        return 'concept'

    @staticmethod
    def get_display_name_for(obj):
        if not obj.names:
            return None
        for name in obj.names:
            if name.locale_preferred:
                return name.name
        return obj.names[0].name

    @staticmethod
    def get_display_locale_for(obj):
        if not obj.names:
            return None
        for name in obj.names:
            if name.locale_preferred:
                return name.locale
        return obj.names[0].locale

    @staticmethod
    def get_version_model():
        return ConceptVersion


class ConceptVersion(ResourceVersionModel):
    concept_class = models.TextField()
    datatype = models.TextField(null=True, blank=True)
    names = ListField(EmbeddedModelField('LocalizedText'))
    descriptions = ListField(EmbeddedModelField('LocalizedText'))
    retired = models.BooleanField(default=False)
    root_version = models.ForeignKey('self', null=True, blank=True)
    is_latest_version = models.BooleanField(default=True)
    version_created_by = models.TextField()
    update_comment = models.TextField(null=True, blank=True)

    def clone(self):
        return ConceptVersion(
            mnemonic='_TEMP',
            public_access=self.public_access,
            concept_class=self.concept_class,
            datatype=self.datatype,
            names=map(lambda n: n.clone(), self.names),
            descriptions=map(lambda d: d.clone(), self.descriptions),
            retired=self.retired,
            versioned_object_id=self.versioned_object_id,
            versioned_object_type=self.versioned_object_type,
            released=self.released,
            previous_version=self,
            parent_version=self.parent_version,
            root_version=self.root_version,
            is_latest_version=self.is_latest_version,
            extras=self.extras
        )

    @property
    def name(self):
        return self.versioned_object.mnemonic

    @property
    def owner_url(self):
        owner = self.versioned_object.owner
        if hasattr(owner, 'username'):
            kwargs = {'user': self.versioned_object.owner.username}
            return reverse('userprofile-detail', kwargs=kwargs)
        else:
            return reverse_resource(self.versioned_object.owner, 'organization-detail')

    @property
    def owner_name(self):
        return self.versioned_object.owner_name

    @property
    def owner_type(self):
        return self.versioned_object.owner_type

    @property
    def display_name(self):
        return Concept.get_display_name_for(self)

    @property
    def display_locale(self):
        return Concept.get_display_locale_for(self)

    @property
    def source(self):
        return self.versioned_object.parent

    @property
    def mappings_url(self):
        return reverse_resource(self.versioned_object, 'mapping-list')

    @property
    def names_for_default_locale(self):
        names = []
        for name in self.names:
            if settings.DEFAULT_LOCALE == name.locale:
                names.append(name.name)
        return names

    @property
    def descriptions_for_default_locale(self):
        descriptions = []
        for desc in self.descriptions:
            if settings.DEFAULT_LOCALE == desc.locale:
                descriptions.append(desc.name)
        return descriptions

    @property
    def is_root_version(self):
        return self == self.root_version

    @classmethod
    def get_latest_version_of(cls, concept):
        versions = ConceptVersion.objects.filter(versioned_object_id=concept.id, is_latest_version=True).order_by('-created_at')
        return versions[0] if versions else None

    @classmethod
    def for_concept(cls, concept, label, previous_version=None, parent_version=None):
        return ConceptVersion(
            mnemonic=label,
            public_access=concept.public_access,
            concept_class=concept.concept_class,
            datatype=concept.datatype,
            extras=concept.extras,
            names=concept.names,
            descriptions=concept.descriptions,
            retired=concept.retired,
            versioned_object_id=concept.id,
            versioned_object_type=ContentType.objects.get_for_model(Concept),
            released=False,
            previous_version=previous_version,
            parent_version=parent_version,
            version_created_by=concept.owner_name
        )

    @classmethod
    def persist_clone(cls, obj, **kwargs):
        errors = dict()
        previous_version = obj.previous_version
        previous_was_latest = previous_version.is_latest_version and obj.is_latest_version
        source_version = SourceVersion.get_latest_version_of(obj.versioned_object.parent)
        persisted = False
        errored_action = 'saving new concept version'
        try:
            obj.save(**kwargs)
            obj.mnemonic = obj.id
            obj.save()

            errored_action = "updating 'is_latest_version' attribute on previous version"
            if previous_was_latest:
                previous_version.is_latest_version = False
                previous_version.save()

            errored_action = 'replacing previous version in latest version of source'
            source_version.update_concept_version(obj)
            source_version.save()

            # Mark versioned object as updated
            obj.versioned_object.save()

            persisted = True
        finally:
            if not persisted:
                source_version.update_concept_version(obj.previous_version)
                if previous_was_latest:
                    previous_version.is_latest_version = True
                    previous_version.save()
                obj.delete()
                errors['non_field_errors'] = ['An error occurred while %s.' % errored_action]
        return errors

    @classmethod
    def resource_type(cls):
        return VERSION_TYPE

    @classmethod
    def versioned_resource_type(cls):
        return CONCEPT_TYPE

    @staticmethod
    def get_url_kwarg():
        return 'concept_version'


class ConceptReference(SubResourceBaseModel, DictionaryItemMixin):
    concept = models.ForeignKey(Concept)
    concept_version = models.ForeignKey(ConceptVersion, null=True, blank=True)
    source_version = models.ForeignKey(SourceVersion, null=True, blank=True)

    def clean(self):
        if self.concept_version and self.source_version:
            raise ValidationError('Cannot specify both source_version and concept_version.')

    @property
    def concept_reference_url(self):
        if self.source_version:
            source_version_url = reverse_resource_version(self.source_version, 'sourceversion-detail')
            return urljoin(source_version_url, 'concepts/%s/' % self.concept.mnemonic)
        if self.concept_version:
            return reverse_resource_version(self.concept_version, 'conceptversion-detail')
        return reverse_resource(self.concept, 'concept-detail')

    @property
    def concept_class(self):
        return self.concept.concept_class if self.concept else None

    @property
    def data_type(self):
        return self.concept.datatype if self.concept else None

    @property
    def source(self):
        return self.concept.parent if self.concept else None

    @property
    def collection(self):
        return self.parent.mnemonic if self.parent else None

    @property
    def owner_name(self):
        return self.parent.parent_resource if self.parent else None

    @property
    def owner_type(self):
        return self.parent.parent_resource_type if self.parent else None

    @property
    def owner_url(self):
        return self.parent.parent_url if self.parent else None

    @property
    def display_name(self):
        return self.concept.display_name if self.concept else None

    @property
    def display_locale(self):
        return self.concept.display_locale if self.concept else None

    @property
    def is_current_version(self):
        return not(self.concept_version or self.source_version)

    @staticmethod
    def get_url_kwarg():
        return 'concept'


@receiver(post_save, sender=User)
def propagate_owner_status(sender, instance=None, created=False, **kwargs):
    if instance.is_active:
        for concept in Concept.objects.filter(owner=instance):
            concept.undelete()
    else:
        for concept in Concept.objects.filter(owner=instance):
            concept.soft_delete()

@receiver(post_save, sender=Source)
def propagate_public_access(sender, instance=None, created=False, **kwargs):
    for concept in Concept.objects.filter(parent_id=instance.id):
        if concept.public_access != instance.public_access:
            concept.public_access = instance.public_access
            for concept_version in ConceptVersion.objects.filter(versioned_object_id=concept.id):
                concept_version.public_access = concept.public_access
                concept_version.save()
            concept.save()

@receiver(post_save, sender=ConceptVersion)
def update_references(sender, instance=None, created=False, **kwargs):
    #Update all references...

    # WHERE concept_version_id = this ConceptVersion ID
    or_clauses = [Q(concept_version_id=instance.id)]

    # OR concept_id = this ConceptVersion's Concept ID,
    #    AND source_version_id refers to one of the source versions containing this ConceptVersion
    concept = instance.versioned_object
    source_versions = SourceVersion.objects.filter(versioned_object_id=concept.parent_id)
    source_versions_with_concept = []
    for source_version in source_versions:
        if instance.id in source_version.concepts:
            source_versions_with_concept.append(source_version.id)
    or_clauses.append(Q(concept_id=concept.id, source_version_id__in=source_versions_with_concept))

    # Do the update
    for ref in ConceptReference.objects.filter(reduce(lambda x, y: x | y, or_clauses[1:], or_clauses[0])):
        ref.save()

    # OR if ConceptVersion is the latest version
    if instance.is_latest_version:
        # Updated all references that don't specify a concept version
        for ref in ConceptReference.objects.filter(concept_id=instance.versioned_object_id):
            if ref.is_current_version:
                ref.save()
