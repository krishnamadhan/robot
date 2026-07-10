#!/usr/bin/env python3
"""Robot movement library for 2WD chassis with L298N driver.

Pin mapping (GPIO BCM):
  Left motor:  forward=18 (IN2), backward=17 (IN1)
  Right motor: forward=22 (IN4), backward=27 (IN3)
"""
from gpiozero import Robot
from time import sleep

LEFT_SPEED       = 0.40
RIGHT_SPEED      = 0.65
LEFT_SPEED_BACK  = 0.40
RIGHT_SPEED_BACK = 0.65

_robot = Robot(left=(18, 17), right=(22, 27))

def forward(duration=None):
    _robot.left_motor.forward(LEFT_SPEED)
    _robot.right_motor.forward(RIGHT_SPEED)
    if duration:
        sleep(duration)
        stop()

def backward(duration=None):
    _robot.left_motor.backward(LEFT_SPEED_BACK)
    _robot.right_motor.backward(RIGHT_SPEED_BACK)
    if duration:
        sleep(duration)
        stop()

def left(duration=None):
    _robot.left_motor.backward(LEFT_SPEED)
    _robot.right_motor.forward(RIGHT_SPEED)
    if duration:
        sleep(duration)
        stop()

def right(duration=None):
    _robot.left_motor.forward(LEFT_SPEED)
    _robot.right_motor.backward(RIGHT_SPEED)
    if duration:
        sleep(duration)
        stop()

def stop():
    _robot.stop()

def cleanup():
    _robot.close()

if __name__ == '__main__':
    print('Testing: forward 1s...')
    forward(1)
    sleep(0.3)
    print('backward 1s...')
    backward(1)
    sleep(0.3)
    print('left 0.5s...')
    left(0.5)
    sleep(0.3)
    print('right 0.5s...')
    right(0.5)
    print('Done.')
    cleanup()
