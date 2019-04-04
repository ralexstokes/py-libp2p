import asyncio
import multiaddr
import pytest
import struct

from threading import Thread
from tests.utils import cleanup
from libp2p import new_node
from libp2p.peer.peerinfo import info_from_p2p_addr
from libp2p.pubsub.pubsub import Pubsub
from libp2p.pubsub.floodsub import FloodSub
from msg_ordering_node import MsgOrderingNode

# pylint: disable=too-many-locals

async def connect(node1, node2):
    # node1 connects to node2
    addr = node2.get_addrs()[0]
    info = info_from_p2p_addr(addr)
    await node1.connect(info)

def create_setup_in_new_thread_func(dummy_node):
    def setup_in_new_thread():
        asyncio.ensure_future(dummy_node.setup_crypto_networking())
    return setup_in_new_thread

async def perform_test(num_nodes, adjacency_map, action_func, assertion_func):
    """
    Helper function to allow for easy construction of custom tests for dummy account nodes
    in various network topologies
    :param num_nodes: number of nodes in the test
    :param adjacency_map: adjacency map defining each node and its list of neighbors
    :param action_func: function to execute that includes actions by the nodes, 
    such as send crypto and set crypto
    :param assertion_func: assertions for testing the results of the actions are correct
    """

    # Create nodes
    dummy_nodes = []
    for i in range(num_nodes):
        dummy_nodes.append(await MsgOrderingNode.create())

    # Create network
    for source_num in adjacency_map:
        target_nums = adjacency_map[source_num]
        for target_num in target_nums:
            await connect(dummy_nodes[source_num].libp2p_node, \
                dummy_nodes[target_num].libp2p_node)

    # Allow time for network creation to take place
    await asyncio.sleep(0.25)

    # Start a thread for each node so that each node can listen and respond
    # to messages on its own thread, which will avoid waiting indefinitely
    # on the main thread. On this thread, call the setup func for the node,
    # which subscribes the node to the CRYPTO_TOPIC topic
    for dummy_node in dummy_nodes:
        thread = Thread(target=create_setup_in_new_thread_func(dummy_node))
        thread.run()

    # Allow time for nodes to subscribe to CRYPTO_TOPIC topic
    await asyncio.sleep(0.25)

    # Perform action function
    await action_func(dummy_nodes)

    # Allow time for action function to be performed (i.e. messages to propogate)
    await asyncio.sleep(1)

    # Perform assertion function
    for dummy_node in dummy_nodes:
        await assertion_func(dummy_node)

    # Success, terminate pending tasks.
    await cleanup()

@pytest.mark.asyncio
async def test_simple_two_nodes_one_word():
    num_nodes = 2
    adj_map = {0: [1]}

    async def action_func(dummy_nodes):
        await dummy_nodes[0].publish_bee_movie_word("aspyn")
        # await asyncio.sleep(0.25)
        await dummy_nodes[0].publish_bee_movie_word("hello")
        # await asyncio.sleep(0.25)

    async def assertion_func(dummy_node):
        next_word = await dummy_node.get_next_word_in_bee_movie()
        assert next_word == "aspyn"
        next_word = await dummy_node.get_next_word_in_bee_movie()
        assert next_word == "hello"

    await perform_test(num_nodes, adj_map, action_func, assertion_func)

@pytest.mark.asyncio
async def test_simple_two_nodes_ten_words():
    num_nodes = 2
    adj_map = {0: [1]}

    words = ["aspyn", "is", "so", "good", "at", "writing", "code", "XD", ":)", "foobar"]

    async def action_func(dummy_nodes):
        for word in words:
            await dummy_nodes[0].publish_bee_movie_word(word)
            # await asyncio.sleep(0.25)

    async def assertion_func(dummy_node):
        for word in words:
            assert await dummy_node.get_next_word_in_bee_movie() == word

    await perform_test(num_nodes, adj_map, action_func, assertion_func)

@pytest.mark.asyncio
async def test_simple_two_nodes_two_words_out_of_order_ids():
    num_nodes = 2
    adj_map = {0: [1]}

    async def action_func(dummy_nodes):
        await dummy_nodes[0].publish_bee_movie_word("word 2", struct.pack('>I', 2))
        word, _, _ = await asyncio.gather(dummy_nodes[0].get_next_word_in_bee_movie(),\
            asyncio.sleep(0.25), \
            dummy_nodes[0].publish_bee_movie_word("word 1", struct.pack('>I', 1)))
        assert word == "word 1"
        assert await dummy_nodes[0].get_next_word_in_bee_movie() == "word 2"

    async def assertion_func(dummy_node):
        pass

    await perform_test(num_nodes, adj_map, action_func, assertion_func)

@pytest.mark.asyncio
async def test_simple_two_nodes_two_words_read_then_publish_out_of_order_ids():
    num_nodes = 2
    adj_map = {0: [1]}
    collected = None

    async def collect_all_words(expected_len, dummy_node):
        collected_words = []
        while True:
            word = await dummy_node.get_next_word_in_bee_movie()
            collected_words.append(word)
            if len(collected_words) == expected_len:
                return collected_words

    async def action_func(dummy_nodes):
        words, _, _, _ = await asyncio.gather(collect_all_words(2, dummy_nodes[0]),\
            asyncio.sleep(0.25), \
            dummy_nodes[0].publish_bee_movie_word("word 2", struct.pack('>I', 2)),\
            dummy_nodes[0].publish_bee_movie_word("word 1", struct.pack('>I', 1)))
        
        # Store collected words to be checked in assertion func
        nonlocal collected
        collected = words

    async def assertion_func(dummy_node):
        assert collected[0] == "word 1"
        assert collected[1] == "word 2"

    await perform_test(num_nodes, adj_map, action_func, assertion_func)

@pytest.mark.asyncio
async def test_simple_two_nodes_ten_words_out_of_order_ids():
    num_nodes = 2
    adj_map = {0: [1]}
    collected = None

    async def collect_all_words(expected_len, dummy_node):
        collected_words = []
        while True:
            word = await dummy_node.get_next_word_in_bee_movie()
            collected_words.append(word)
            if len(collected_words) == expected_len:
                return collected_words

    async def action_func(dummy_nodes):
        words = ["e", "b", "d", "i", "a", "h", "c", "f", "g", "j"]
        msg_id_nums = [5, 2, 4, 9, 1, 8, 3, 6, 7, 10]
        msg_ids = []
        tasks = []
        for msg_id_num in msg_id_nums:
            msg_ids.append(struct.pack('>I', msg_id_num))

        tasks.append(collect_all_words(len(words), dummy_nodes[0]))
        tasks.append(asyncio.sleep(0.25))

        for i in range(len(words)):
            tasks.append(dummy_nodes[0].publish_bee_movie_word(words[i], msg_ids[i]))    

        res = await asyncio.gather(*tasks)
        
        nonlocal collected
        collected = res[0]

    async def assertion_func(dummy_node):
        correct_words = ["a", "b", "c", "d", "e", "f", "g", "h", "i", "j"]
        for i in range(len(correct_words)):
            assert collected[i] == correct_words[i]
        assert len(collected) == len(correct_words)

    await perform_test(num_nodes, adj_map, action_func, assertion_func)
