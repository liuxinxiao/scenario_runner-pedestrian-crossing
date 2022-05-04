#!/usr/bin/env python

# Copyright (c) 2018-2020 Intel Corporation
#
# This work is licensed under the terms of the MIT license.
# For a copy, see <https://opensource.org/licenses/MIT>.

"""
Scenario in which the ego is parked between two vehicles and has to maneuver to start the route.
"""

from __future__ import print_function

import py_trees
import carla


from srunner.scenariomanager.carla_data_provider import CarlaDataProvider
from srunner.scenariomanager.scenarioatomics.atomic_behaviors import SwitchOutsideRouteLanesTest, ActorTransformSetter, ActorDestroy
from srunner.scenariomanager.scenarioatomics.atomic_criteria import CollisionTest
from srunner.scenariomanager.scenarioatomics.atomic_trigger_conditions import DriveDistance
from srunner.scenarios.basic_scenario import BasicScenario


def convert_dict_to_location(actor_dict):
    """
    Convert a JSON string to a Carla.Location
    """
    location = carla.Location(
        x=float(actor_dict['x']),
        y=float(actor_dict['y']),
        z=float(actor_dict['z'])
    )
    return location


class ParkingExit(BasicScenario):
    """
    This class holds everything required for a scenario in which the ego would be teleported to the parking lane.
    Once the scenario is triggered, the OutsideRouteLanesTest will be deactivated since the ego is out of the driving lane.
    Then blocking vehicles will be generated in front of and behind the parking point.
    The ego need to exit from the parking lane and then merge into the driving lane.
    After the ego is {end_distance} meters away from the parking point, the OutsideRouteLanesTest will be activated and the scenario ends.

    Note 1: For route mode, this shall be the first scenario of the route. The trigger point shall be the first point of the route waypoints.

    Note 2: Make sure there are enough space for spawning blocking vehicles.
    """

    def __init__(self, world, ego_vehicles, config, debug_mode=False, criteria_enable=True,
                 timeout=180):
        """
        Setup all relevant parameters and create scenario
        and instantiate scenario manager
        """
        self._world = world
        self._map = CarlaDataProvider.get_map()
        self.timeout = timeout

        if 'front_vehicle_distance' in config.other_parameters:
            self._front_vehicle_distance = float(
                config.other_parameters['front_vehicle_distance']['value'])
        else:
            self._front_vehicle_distance = 20  # m

        if 'behind_vehicle_distance' in config.other_parameters:
            self._behind_vehicle_distance = float(
                config.other_parameters['behind_vehicle_distance']['value'])
        else:
            self._behind_vehicle_distance = 5  # m

        self._end_distance = self._front_vehicle_distance + 15

        if 'parking_lane_side' in config.other_parameters:
            self._parking_lane_side = config.other_parameters['parking_lane_side']['value']
        else:
            self._parking_lane_side = "right"

        # Get parking_waypoint based on trigger_point
        self._trigger_location = config.trigger_points[0].location
        self._reference_waypoint = self._map.get_waypoint(self._trigger_location)
        if self._parking_lane_side == "left":
            self._parking_waypoint = self._reference_waypoint.get_left_lane()
        else:
            self._parking_waypoint = self._reference_waypoint.get_right_lane()

        if self._parking_waypoint is None:
            raise Exception(
                "Couldn't find parking point on the {} side".format(self._parking_lane_side))

        super(ParkingExit, self).__init__("ParkingExit",
                                          ego_vehicles,
                                          config,
                                          world,
                                          debug_mode,
                                          criteria_enable=criteria_enable)

    def _initialize_actors(self, config):
        """
        Custom initialization
        """
        # Spawn the actor in front of the ego
        front_points = self._parking_waypoint.next(
            self._front_vehicle_distance)
        if not front_points:
            raise ValueError(
                "Couldn't find viable position for the vehicle in front of the parking point")

        actor_front = CarlaDataProvider.request_new_actor(
            'vehicle.*', front_points[0].transform, rolename='scenario', attribute_filter={'base_type': 'car'})
        if actor_front is None:
            raise ValueError(
                "Couldn't spawn the vehicle in front of the parking point")
        self.other_actors.append(actor_front)

        # And move it to the side
        side_transform = self._get_displaced_transform(actor_front, front_points[0])
        actor_front.set_location(side_transform)

        # Spawn the actor behind the ego
        behind_points = self._parking_waypoint.previous(
            self._behind_vehicle_distance)
        if not behind_points:
            raise ValueError(
                "Couldn't find viable position for the vehicle behind the parking point")

        actor_behind = CarlaDataProvider.request_new_actor(
            'vehicle.*', behind_points[0].transform, rolename='scenario', attribute_filter={'base_type': 'car'})
        if actor_behind is None:
            raise ValueError(
                "Couldn't spawn the vehicle behind the parking point")
        self.other_actors.append(actor_behind)

        # And move it to the side
        side_transform = self._get_displaced_transform(actor_behind, behind_points[0])
        actor_behind.set_location(side_transform)

        # Move the ego to its side position
        self._ego_transform = self._get_displaced_transform(self.ego_vehicles[0], self._parking_waypoint)
        self.ego_vehicles[0].set_location(self._ego_transform)

    def _get_displaced_transform(self, actor, wp):
        """
        Calculates the transforming such that the actor is at the sidemost part of the lane
        """
        # Move the actor to the edge of the lane near the sidewalk
        displacement = (wp.lane_width - actor.bounding_box.extent.y) / 4
        displacement_vector = wp.transform.get_right_vector()
        if self._parking_lane_side == 'left':
            displacement_vector *= -1

        new_location = wp.transform.location + carla.Location(x=displacement*displacement_vector.x,
                                                              y=displacement*displacement_vector.y,
                                                              z=displacement*displacement_vector.z)
        new_location.z += 0.05  # Just in case, avoid collisions with the ground
        return new_location

    def _create_behavior(self):
        """
        Deactivate OutsideRouteLanesTest, then move ego to the parking point,
        generate blocking vehicles in front of and behind the ego.
        After ego drives away, activate OutsideRouteLanesTest, end scenario.
        """

        sequence = py_trees.composites.Sequence()

        # Deactivate OutsideRouteLanesTest
        sequence.add_child(SwitchOutsideRouteLanesTest(False))

        root = py_trees.composites.Parallel(
            policy=py_trees.common.ParallelPolicy.SUCCESS_ON_ONE)

        end_condition = py_trees.composites.Sequence()
        end_condition.add_child(DriveDistance(
            self.ego_vehicles[0], self._end_distance))
        root.add_child(end_condition)
        sequence.add_child(root)

        sequence.add_child(SwitchOutsideRouteLanesTest(True))

        for actor in self.other_actors:
            sequence.add_child(ActorDestroy(actor))

        return sequence

    def _create_test_criteria(self):
        """
        A list of all test criteria will be created that is later used
        in parallel behavior tree.
        """
        if self.route_mode:
            return[]

        return [CollisionTest(self.ego_vehicles[0])]

    def __del__(self):
        """
        Remove all actors and traffic lights upon deletion
        """
        self.remove_all_actors()
