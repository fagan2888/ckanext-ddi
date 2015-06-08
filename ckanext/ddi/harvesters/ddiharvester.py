# -*- coding: utf-8 -*-

import requests

from ckan.lib.helpers import json
from ckanext.harvest.model import HarvestObject
from ckanext.harvest.harvesters import HarvesterBase
from ckanext.ddi.importer import DdiCkanMetadata

from pylons import config

import logging
log = logging.getLogger(__name__)


class NadaHarvester(HarvesterBase):
    '''
    The harvester for DDI data
    '''

    HARVEST_USER = 'harvest'

    ACCESS_TYPES = {
        'direct_access': 1,
        'public_use': 2,
        'licensed': 3,
        'data_enclave': 4,
        'data_external': 5,
        'no_data_available': 6,
    }

    DEFAULT_ATTRIBUTES = [
        'id',
        'name',
        'title',
        'url',
        'author',
        'author_email',
        'maintainer',
        'maintainer_email',
        'license_id',
        'version',
        'notes',
        'tags',
        'extras',
    ]

    def info(self):
        return {
            'name': 'nada',
            'title': 'NADA harvester for DDI',
            'description': (
                'Harvests DDI data from a NADA instance '
                '(survey cataloguing software).'
            ),
            'form_config_interface': 'Text'
        }

    def _set_config(self, config_str):
        if config_str:
            self.config = json.loads(config_str)
        else:
            self.config = {}

        if 'user' not in self.config:
            self.config['user'] = self.HARVEST_USER

        log.debug('Using config: %r' % self.config)

    def _get_search_api(self, access_type=None):
        if access_type is not None:
            try:
                return (
                    '/index.php/api/v2/catalog/search/format/json/?dtype[]=%s'
                    % self.ACCESS_TYPES[access_type]
                )
            except KeyError:
                raise AccessTypeNotAvailableError(
                    'Access type %s not available. Available types: %s'
                    % (access_type, self.ACCESS_TYPES)
                )
        else:
            return '/index.php/api/v2/catalog/search/format/json'

    def _get_ddi_api(self, ddi_id):
        return '/index.php/catalog/ddi/%s' % ddi_id

    def _get_catalog_path(self, ddi_id):
        return '/index.php/catalog/%s' % ddi_id

    def gather_stage(self, harvest_job):
        log.debug('In NadaHarvester gather_stage')
        api_url = None
        try:
            self._set_config(harvest_job.source.config)
            base_url = harvest_job.source.url.rstrip('/')

            try:
                api_url = base_url + self._get_search_api(
                    self.config['access_type']
                )
            except (AccessTypeNotAvailableError, KeyError):
                api_url = base_url + self._get_search_api('public_use')

            log.debug('Gather datasets from: %s' % api_url)

            r = requests.get(api_url)
            data = r.json()
            log.debug('JSON data from %s: %r' % (api_url, data))

            harvest_obj_ids = []
            for row in data['rows']:
                harvest_obj = HarvestObject(
                    guid=row['id'],
                    job=harvest_job
                )
                harvest_obj.save()
                harvest_obj_ids.append(harvest_obj.id)

            log.debug('IDs: %r' % harvest_obj_ids)

            return harvest_obj_ids
        except Exception, e:
            self._save_gather_error(
                'Unable to get content for URL: %s: %s'
                % (api_url, str(e)),
                harvest_job
            )

    def fetch_stage(self, harvest_object):
        log.debug('In NadaHarvester fetch_stage')
        self._set_config(harvest_object.job.source.config)

        if not harvest_object:
            log.error('No harvest object received')
            self._save_object_error(
                'No harvest object received',
                harvest_object
            )
            return False

        base_url = harvest_object.source.url.rstrip('/')
        ddi_api_url = None
        try:
            ddi_api_url = base_url + self._get_ddi_api(harvest_object.guid)
            log.debug('Fetching content from %s' % ddi_api_url)
            r = requests.get(ddi_api_url)
            harvest_object.content = r.text
            harvest_object.save()
            log.debug('successfully processed ' + harvest_object.guid)
            return True
        except Exception, e:
            self._save_object_error(
                'Unable to get content for package: %s: %r' % (ddi_api_url, e),
                harvest_object
            )
            return False

    def import_stage(self, harvest_object):
        log.debug('In NadaHarvester import_stage')
        self._set_config(harvest_object.job.source.config)

        if not harvest_object:
            log.error('No harvest object received')
            self._save_object_error(
                'No harvest object received',
                harvest_object
            )
            return False

        try:
            base_url = harvest_object.source.url.rstrip('/')
            ckan_metadata = DdiCkanMetadata()
            pkg_dict = ckan_metadata.load(harvest_object.content)
            pkg_dict = self._convert_to_extras(pkg_dict)

            # update URL with NADA catalog link
            catalog_path = self._get_catalog_path(harvest_object.guid)
            pkg_dict['url'] = base_url + catalog_path

            # set license from harvester config or use CKAN instance default
            if 'license' in self.config:
                pkg_dict['license_id'] = self.config['license']
            else:
                pkg_dict['license_id'] = config.get(
                    'ckanext.ddi.default_license',
                    ''
                )

            # add resources
            resources = [{
                'url': base_url + self._get_ddi_api(harvest_object.guid),
                'name': 'DDI XML of %s' % pkg_dict['title'],
                'format': 'xml'
            }]
            pkg_dict['resources'] = resources

            log.debug('package dict: %s' % pkg_dict)
            return self._create_or_update_package(pkg_dict, harvest_object)
        except Exception, e:
            self._save_object_error(
                'Exception in import stage: %r' % (e),
                harvest_object
            )
            return False

    def _convert_to_extras(self, pkg_dict):
        if 'extras' not in pkg_dict:
            pkg_dict['extras'] = []
        keys_to_delete = []
        for key in pkg_dict:
            if key not in self.DEFAULT_ATTRIBUTES:
                log.debug('Converting %s to extra' % key)
                pkg_dict['extras'].append((key, pkg_dict[key]))
                keys_to_delete.append(key)

        for key in keys_to_delete:
            if key in pkg_dict:
                log.debug('Delete key %s from pkg_dict' % key)
                del pkg_dict[key]
        return pkg_dict


class AccessTypeNotAvailableError(Exception):
    pass
