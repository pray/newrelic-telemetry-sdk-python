# Copyright 2019 New Relic, Inc.
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
import time
import threading

_logger = logging.getLogger(__name__)


class Harvester(threading.Thread):
    """Report data to New Relic at a fixed interval

    The Harvester is a thread implementation which sends data to New Relic every
    ``harvest_interval`` seconds or until the data buffers are full.

    The reporter will automatically handle error conditions which may occur
    such as:

    * Network timeouts
    * New Relic errors

    :param client: The client instance to call in order to send data.
    :type client: newrelic_sdk.client.Client
    :param batch: A batch with record and flush interfaces.
    :param harvest_interval: (optional) The interval in seconds at which data
        will be reported. (default 5)
    :type harvest_interval: int or float

    Example::

        >>> import os
        >>> insert_key = os.environ.get("NEW_RELIC_INSERT_KEY", "")
        >>> from newrelic_telemetry_sdk import MetricBatch, MetricClient
        >>> metric_client = MetricClient(insert_key)
        >>> metric_batch = MetricBatch()
        >>> harvester = Harvester(metric_client, metric_batch)
        >>> harvester.start()
        >>> harvester.stop()
    """

    EVENT_CLS = threading.Event

    def __init__(self, client, batch, harvest_interval=5):
        super(Harvester, self).__init__()
        self.daemon = True
        self.batch = batch
        self.harvest_interval = harvest_interval
        self._client = client
        self._harvest_interval_start = 0
        self._shutdown = self.EVENT_CLS()

    def _send(self, items, *args):
        """Send items through the harvester client, handling any exceptions"""
        if items:
            try:
                response = self._client.send_batch(items, *args)
                if not response.ok:
                    _logger.error(
                        "New Relic send_batch failed with status code: %r",
                        response.status,
                    )
                return response
            except Exception:
                _logger.exception("New Relic send_batch failed with an exception.")

    def _wait_for_harvest(self):
        """Tracks and adjusts time required to maintain the harvest interval"""
        current_time = time.time()
        interval_start = self._harvest_interval_start or current_time
        timeout = max(self.harvest_interval - (current_time - interval_start), 0)
        shutdown = self._shutdown.wait(timeout)
        self._harvest_interval_start = time.time()
        return shutdown

    def run(self):
        """Main loop of the harvester thread"""
        while not self._wait_for_harvest():
            self._send(*self.batch.flush())

        # Flush any remaining data and send it prior to shutting down
        self._send(*self.batch.flush())

        # Clear all references to client and batch to close connections and
        # deallocate batch
        self.batch = self._client = None

    def record(self, item):
        """Add an item to the batch to be harvested

        Calling record with an item will merge it into the batch maintained by
        this harvester.

        :param item: A metric or span to be merged into a batch.
        """
        self._batch.record(item)

    def stop(self, timeout=None):
        """Terminate the harvester.

        This will request and wait for the thread to terminate. The thread will
        not terminate immediately since any pending data will be sent.

        When the timeout argument is present, this function will exit after at
        most timeout seconds. The thread may still be alive after this function
        exits if the timeout is reached but the thread hasn't yet terminated.

        :param timeout: (optional) A timeout in seconds to wait for the thread
            to shut down or None to block until the thread exits (default: None)
        :type timeout: int or float
        """
        self._shutdown.set()
        self.join(timeout=timeout)
