# Copyright 2015 Google Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import logging

from package_moduleshelf import get_model, storage
from flask import current_app
from google.cloud import pubsub
import psq
import requests


publisher_client = pubsub.PublisherClient()
subscriber_client = pubsub.SubscriberClient()


def get_package_modules_queue():
    project = current_app.config['PROJECT_ID']

    # Create a queue specifically for processing package_modules and pass in the
    # Flask application context. This ensures that tasks will have access
    # to any extensions / configuration specified to the app, such as
    # models.
    return psq.Queue(
        publisher_client, subscriber_client, project,
        'package_modules', extra_context=current_app.app_context)


def process_package_module(package_module_id):
    """
    Handles an individual package_moduleshelf message by looking it up in the
    model, querying the Google package_modules API, and updating the package_module in the model
    with the info found in the package_modules API.
    """

    model = get_model()

    package_module = model.read(package_module_id)

    if not package_module:
        logging.warn("Could not find package_module with id {}".format(package_module_id))
        return

    if 'title' not in package_module:
        logging.warn("Can't process package_module id {} without a title."
                     .format(package_module_id))
        return

    logging.info("Looking up package_module with title {}".format(package_module[
                                                        'title']))

    new_package_module_data = query_package_modules_api(package_module['title'])

    if not new_package_module_data:
        return

    package_module['title'] = new_package_module_data.get('title')
    package_module['author'] = ', '.join(new_package_module_data.get('authors', []))
    package_module['publishedDate'] = new_package_module_data.get('publishedDate')
    package_module['description'] = new_package_module_data.get('description')

    # If the new package_module data has thumbnail images and there isn't currently a
    # thumbnail for the package_module, then copy the image to cloud storage and update
    # the package_module data.
    if not package_module.get('imageUrl') and 'imageLinks' in new_package_module_data:
        new_img_src = new_package_module_data['imageLinks']['smallThumbnail']
        package_module['imageUrl'] = download_and_upload_image(
            new_img_src,
            "{}.jpg".format(package_module['title']))

    model.update(package_module, package_module_id)


def query_package_modules_api(title):
    """
    Queries the Google package_modules API to find detailed information about the package_module
    with the given title.
    """
    r = requests.get('https://www.googleapis.com/package_modules/v1/volumes', params={
        'q': title
    })

    try:
        data = r.json()['items'][0]['volumeInfo']
        return data

    except KeyError:
        logging.info("No package_module found for title {}".format(title))
        return None

    except ValueError:
        logging.info("Unexpected response from package_modules API: {}".format(r))
        return None


def download_and_upload_image(src, dst_filename):
    """
    Downloads an image file and then uploads it to Google Cloud Storage,
    essentially re-hosting the image in GCS. Returns the public URL of the
    image in GCS
    """
    r = requests.get(src)

    if not r.status_code == 200:
        return

    return storage.upload_file(
        r.content,
        dst_filename,
        r.headers.get('content-type', 'image/jpeg'))
