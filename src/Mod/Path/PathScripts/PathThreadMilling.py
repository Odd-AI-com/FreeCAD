# -*- coding: utf-8 -*-
# ***************************************************************************
# *   Copyright (c) 2019 sliptonic <shopinthewoods@gmail.com>               *
# *                                                                         *
# *   This program is free software; you can redistribute it and/or modify  *
# *   it under the terms of the GNU Lesser General Public License (LGPL)    *
# *   as published by the Free Software Foundation; either version 2 of     *
# *   the License, or (at your option) any later version.                   *
# *   for detail see the LICENCE text file.                                 *
# *                                                                         *
# *   This program is distributed in the hope that it will be useful,       *
# *   but WITHOUT ANY WARRANTY; without even the implied warranty of        *
# *   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the         *
# *   GNU Library General Public License for more details.                  *
# *                                                                         *
# *   You should have received a copy of the GNU Library General Public     *
# *   License along with this program; if not, write to the Free Software   *
# *   Foundation, Inc., 59 Temple Place, Suite 330, Boston, MA  02111-1307  *
# *   USA                                                                   *
# *                                                                         *
# ***************************************************************************

from __future__ import print_function

import FreeCAD
import Path
import PathScripts.PathCircularHoleBase as PathCircularHoleBase
import PathScripts.PathGeom as PathGeom
import PathScripts.PathLog as PathLog
import PathScripts.PathOp as PathOp
import math
from PySide.QtCore import QT_TRANSLATE_NOOP

__title__ = "Path Thread Milling Operation"
__author__ = "sliptonic (Brad Collette)"
__url__ = "http://www.freecadweb.org"
__doc__ = "Path thread milling operation."

# math.sqrt(3)/2 ... 60deg triangle height
SQRT_3_DIVIDED_BY_2 = 0.8660254037844386

if True:
    PathLog.setLevel(PathLog.Level.DEBUG, PathLog.thisModule())
    PathLog.trackModule(PathLog.thisModule())
else:
    PathLog.setLevel(PathLog.Level.INFO, PathLog.thisModule())

translate = FreeCAD.Qt.translate


def threadRadii(internal, majorDia, minorDia, toolDia, toolCrest):
    """threadRadii(majorDia, minorDia, toolDia, toolCrest) ... returns the minimum and maximum radius for thread."""
    PathLog.track(majorDia, minorDia, toolDia, toolCrest)
    if toolCrest is None:
        toolCrest = 0.0
    # As it turns out metric and imperial standard threads follow the same rules.
    # The determining factor is the height of the full 60 degree triangle H.
    # - The minor diameter is 1/4 * H smaller than the pitch diameter.
    # - The major diameter is 3/8 * H bigger than the pitch diameter
    # Since we already have the outer diameter it's simpler to just add 1/8 * H
    # to get the outer tip of the thread.
    H = ((majorDia - minorDia) / 2.0) * 1.6  # (D - d)/2 = 5/8 * H
    if internal:
        # mill inside out
        outerTip = majorDia / 2.0 + H / 8.0
        # Compensate for the crest of the tool
        toolTip = outerTip - toolCrest * SQRT_3_DIVIDED_BY_2
        return ((minorDia - toolDia) / 2.0, toolTip - toolDia / 2.0)
    # mill outside in
    innerTip = minorDia / 2.0 - H / 4.0
    # Compensate for the crest of the tool
    toolTip = innerTip - toolCrest * SQRT_3_DIVIDED_BY_2
    return ((majorDia + toolDia) / 2.0, toolTip + toolDia / 2.0)

def threadPasses(count, radii, internal, majorDia, minorDia, toolDia, toolCrest):
    PathLog.track(count, radii, internal, majorDia, minorDia, toolDia, toolCrest)
    minor, major = radii(internal, majorDia, minorDia, toolDia, toolCrest)
    dr = float(major - minor) / count
    if internal:
        return [minor + dr * (i + 1) for i in range(count)]
    return [major - dr * (i + 1) for i in range(count)]


def elevatorRadius(obj, center, internal, tool):
    '''elevatorLocation(obj, center, internal, tool) ... return suitable location for the tool elevator'''

    if internal:
        dy = float(obj.MinorDiameter - tool.Diameter) / 2 - 1
        if dy < 0:
            if (obj.MinorDiameter < tool.Diameter):
                PathLog.error("The selected tool is too big (d={}) for milling a thread with minor diameter D={}".format(tool.Diameter, obj.MinorDiameter))
            dy = 0
    else:
        dy = float(obj.MajorDiameter + tool.Diameter) / 2 + 1

    return dy

def comment(path, msg):
    if True:
        path.append(Path.Command("(------- {} -------)".format(msg)))

class _ThreadInternal(object):
    """Helper class for dealing with different thread types"""

    def __init__(self, cmd, zStart, zFinal, pitch):
        self.cmd = cmd
        if zStart < zFinal:
            self.pitch = pitch
        else:
            self.pitch = -pitch
        self.hPitch = self.pitch / 2
        self.zStart = zStart
        self.zFinal = zFinal

    def overshoots(self, z):
        """overshoots(z) ... returns true if adding another half helix goes beyond the thread bounds"""
        if self.pitch < 0:
            return z + self.hPitch < self.zFinal
        return z + self.hPitch > self.zFinal

    def adjustX(self, x, dx):
        """adjustX(x, dx) ... move x by dx, the direction depends on the thread settings"""
        if self.isG3() == (self.pitch > 0):
            return x + dx
        return x - dx

    def adjustY(self, y, dy):
        """adjustY(y, dy) ... move y by dy, the direction depends on the thread settings"""
        if self.isG3():
            return y - dy
        return y - dy

    def isG3(self):
        """isG3() ... returns True if this is a G3 command"""
        return self.cmd in ["G3", "G03", "g3", "g03"]

    def isUp(self):
        """isUp() ... returns True if the thread goes from the bottom up"""
        return self.pitch > 0


def threadCommands(center, cmd, zStart, zFinal, pitch, radius, leadInOut, elevator):
    """threadCommands(center, cmd, zStart, zFinal, pitch, radius) ... returns the g-code to mill the given internal thread"""
    thread = _ThreadInternal(cmd, zStart, zFinal, pitch)

    yMin = center.y - radius
    yMax = center.y + radius

    path = []
    # at this point the tool is at a safe heiht (depending on the previous thread), so we can move
    # into position first, and then drop to the start height. If there is any material in the way this
    # op hasn't been setup properly.
    path.append(Path.Command("G0", {"X": center.x, "Y": center.y + elevator}))
    path.append(Path.Command("G0", {"Z": thread.zStart}))
    if leadInOut:
        comment(path, 'lead-in')
        path.append(Path.Command(thread.cmd, {"Y": yMax, "J": (yMax - (center.y + elevator)) / 2}))
        comment(path, 'lead-in')
    else:
        path.append(Path.Command("G1", {"Y": yMax}))

    z = thread.zStart
    r = -radius
    i = 0
    while not PathGeom.isRoughly(z, thread.zFinal):
        if thread.overshoots(z):
            break
        if 0 == (i & 0x01):
            y = yMin
        else:
            y = yMax
        path.append(Path.Command(thread.cmd, {"Y": y, "Z": z + thread.hPitch, "J": r}))
        r = -r
        i = i + 1
        z = z + thread.hPitch

    if PathGeom.isRoughly(z, thread.zFinal):
        x = center.x
        y = yMin if 0 == (i & 0x01) else yMax
    else:
        n = math.fabs(thread.zFinal - thread.zStart) / thread.hPitch
        k = n - int(n)
        dy = math.cos(k * math.pi)
        dx = math.sin(k * math.pi)
        y = thread.adjustY(center.y, r * dy)
        x = thread.adjustX(center.x, r * dx)
        comment(path, 'finish-thread')
        path.append(
            Path.Command(thread.cmd, {"X": x, "Y": y, "Z": thread.zFinal, "J": r})
        )
        comment(path, 'finish-thread')

    a = math.atan2(y - center.y, x - center.x)
    dx = math.cos(a) * elevator
    dy = math.sin(a) * elevator

    if leadInOut:
        comment(path, 'lead-out')
        path.append(
            Path.Command(
                thread.cmd,
                {"X": center.x + dx, "Y": center.y + dy, "I": dx / 2, "J": dy / 2},
            )
        )
        comment(path, 'lead-out')

    path.append(Path.Command("G1", {"X": center.x + dx, "Y": center.y - dy}))

    return path



class ObjectThreadMilling(PathCircularHoleBase.ObjectOp):
    """Proxy object for thread milling operation."""

    LeftHand = "LeftHand"
    RightHand = "RightHand"
    ThreadTypeCustomExternal = "CustomExternal"
    ThreadTypeCustomInternal = "CustomInternal"
    ThreadTypeImperialExternal2A = "ImperialExternal2A"
    ThreadTypeImperialExternal3A = "ImperialExternal3A"
    ThreadTypeImperialInternal2B = "ImperialInternal2B"
    ThreadTypeImperialInternal3B = "ImperialInternal3B"
    ThreadTypeMetricExternal4G6G = "MetricExternal4G6G"
    ThreadTypeMetricExternal6G = "MetricExternal6G"
    ThreadTypeMetricInternal6H = "MetricInternal6H"
    DirectionClimb = "Climb"
    DirectionConventional = "Conventional"

    ThreadOrientations = [LeftHand, RightHand]

    ThreadTypeData = {
            ThreadTypeImperialExternal2A : 'imperial-external-2A.csv',
            ThreadTypeImperialExternal3A : 'imperial-external-3A.csv',
            ThreadTypeImperialInternal2B : 'imperial-internal-2B.csv',
            ThreadTypeImperialInternal3B : 'imperial-internal-3B.csv',
            ThreadTypeMetricExternal4G6G : 'metric-external-4G6G.csv',
            ThreadTypeMetricExternal6G : 'metric-external-6G.csv',
            ThreadTypeMetricInternal6H : 'metric-internal-6H.csv',
            }

    ThreadTypesExternal = [
        ThreadTypeCustomExternal,
        ThreadTypeImperialExternal2A,
        ThreadTypeImperialExternal3A,
        ThreadTypeMetricExternal4G6G,
        ThreadTypeMetricExternal6G,
        ]
    ThreadTypesInternal = [
        ThreadTypeCustomInternal,
        ThreadTypeImperialInternal2B,
        ThreadTypeImperialInternal3B,
        ThreadTypeMetricInternal6H,
        ]
    ThreadTypesImperial = [
        ThreadTypeImperialExternal2A,
        ThreadTypeImperialExternal3A,
        ThreadTypeImperialInternal2B,
        ThreadTypeImperialInternal3B,
        ]
    ThreadTypesMetric = [
        ThreadTypeMetricExternal4G6G,
        ThreadTypeMetricExternal6G,
        ThreadTypeMetricInternal6H,
        ]
    ThreadTypes = ThreadTypesInternal + ThreadTypesExternal
    Directions = [DirectionClimb, DirectionConventional]

    @classmethod
    def propertyEnumerations(self, dataType="data"):
        """helixOpPropertyEnumerations(dataType="data")... return property enumeration lists of specified dataType.
        Args:
            dataType = 'data', 'raw', 'translated'
        Notes:
        'data' is list of internal string literals used in code
        'raw' is list of (translated_text, data_string) tuples
        'translated' is list of translated string literals
        """
        PathLog.track()

        # Enumeration lists for App::PropertyEnumeration properties
        enums = {
            "ThreadType": [
                (translate("Path_ThreadMilling", "Custom External"), ObjectThreadMilling.ThreadTypeCustomExternal),
                (translate("Path_ThreadMilling", "Custom Internal"), ObjectThreadMilling.ThreadTypeCustomInternal),
                (translate("Path_ThreadMilling", "Imperial External (2A)"), ObjectThreadMilling.ThreadTypeImperialExternal2A),
                (translate("Path_ThreadMilling", "Imperial External (3A)"), ObjectThreadMilling.ThreadTypeImperialExternal3A),
                (translate("Path_ThreadMilling", "Imperial Internal (2B)"), ObjectThreadMilling.ThreadTypeImperialInternal2B),
                (translate("Path_ThreadMilling", "Imperial Internal (3B)"), ObjectThreadMilling.ThreadTypeImperialInternal3B),
                (translate("Path_ThreadMilling", "Metric External (4G6G)"), ObjectThreadMilling.ThreadTypeMetricExternal4G6G),
                (translate("Path_ThreadMilling", "Metric External (6G)"), ObjectThreadMilling.ThreadTypeMetricExternal6G),
                (translate("Path_ThreadMilling", "Metric Internal (6H)"), ObjectThreadMilling.ThreadTypeMetricInternal6H),
            ],
            "ThreadOrientation": [
                (translate("Path_ThreadMilling", "LeftHand"), ObjectThreadMilling.LeftHand),
                (translate("Path_ThreadMilling", "RightHand"), ObjectThreadMilling.RightHand),
            ],
            "Direction": [
                (translate("Path_ThreadMilling", "Climb"), ObjectThreadMilling.DirectionClimb),
                (translate("Path_ThreadMilling", "Conventional"), ObjectThreadMilling.DirectionConventional),
            ],
        }

        if dataType == "raw":
            return enums

        data = list()
        idx = 0 if dataType == "translated" else 1

        PathLog.debug(enums)

        for k, v in enumerate(enums):
            data.append((v, [tup[idx] for tup in enums[v]]))
        PathLog.debug(data)

        return data

    def circularHoleFeatures(self, obj):
        PathLog.track()
        return PathOp.FeatureBaseGeometry

    def initCircularHoleOperation(self, obj):
        PathLog.track()
        obj.addProperty(
            "App::PropertyEnumeration",
            "ThreadOrientation",
            "Thread",
            QT_TRANSLATE_NOOP("App::Property", "Set thread orientation"),
        )
        # obj.ThreadOrientation = self.ThreadOrientations
        obj.addProperty(
            "App::PropertyEnumeration",
            "ThreadType",
            "Thread",
            QT_TRANSLATE_NOOP("App::Property", "Currently only internal"),
        )
        # obj.ThreadType = self.ThreadTypes
        obj.addProperty(
            "App::PropertyString",
            "ThreadName",
            "Thread",
            QT_TRANSLATE_NOOP(
                "App::Property", "Defines which standard thread was chosen"
            ),
        )
        obj.addProperty(
            "App::PropertyLength",
            "MajorDiameter",
            "Thread",
            QT_TRANSLATE_NOOP("App::Property", "Set thread's major diameter"),
        )
        obj.addProperty(
            "App::PropertyLength",
            "MinorDiameter",
            "Thread",
            QT_TRANSLATE_NOOP("App::Property", "Set thread's minor diameter"),
        )
        obj.addProperty(
            "App::PropertyLength",
            "Pitch",
            "Thread",
            QT_TRANSLATE_NOOP(
                "App::Property", "Set thread's pitch - used for metric threads"
            ),
        )
        obj.addProperty(
            "App::PropertyInteger",
            "TPI",
            "Thread",
            QT_TRANSLATE_NOOP(
                "App::Property",
                "Set thread's TPI (turns per inch) - used for imperial threads",
            ),
        )
        obj.addProperty(
            "App::PropertyInteger",
            "ThreadFit",
            "Thread",
            QT_TRANSLATE_NOOP(
                "App::Property", "Set how many passes are used to cut the thread"
            ),
        )
        obj.addProperty(
            "App::PropertyInteger",
            "Passes",
            "Operation",
            QT_TRANSLATE_NOOP(
                "App::Property", "Set how many passes are used to cut the thread"
            ),
        )
        obj.addProperty(
            "App::PropertyEnumeration",
            "Direction",
            "Operation",
            QT_TRANSLATE_NOOP("App::Property", "Direction of thread cutting operation"),
        )
        obj.addProperty(
            "App::PropertyBool",
            "LeadInOut",
            "Operation",
            QT_TRANSLATE_NOOP(
                "App::Property",
                "Set to True to get lead in and lead out arcs at the start and end of the thread cut",
            ),
        )
        obj.addProperty(
            "App::PropertyLink",
            "ClearanceOp",
            "Operation",
            QT_TRANSLATE_NOOP(
                "App::Property", "Operation to clear the inside of the thread"
            ),
        )

        for n in self.propertyEnumerations():
            setattr(obj, n[0], n[1])

    def _isThreadInternal(self, obj):
        return obj.ThreadType in self.ThreadTypesInternal

    def _threadSetupInternal(self, obj):
        PathLog.track()
        # the thing to remember is that Climb, for an internal thread must always be G3
        if obj.Direction == self.DirectionClimb:
            if obj.ThreadOrientation == self.RightHand:
                return ("G3", obj.FinalDepth.Value, obj.StartDepth.Value)
            return ("G3", obj.StartDepth.Value, obj.FinalDepth.Value)
        if obj.ThreadOrientation == self.RightHand:
            return ("G2", obj.StartDepth.Value, obj.FinalDepth.Value)
        return ("G2", obj.FinalDepth.Value, obj.StartDepth.Value)

    def threadSetup(self, obj):
        PathLog.track()
        cmd, zbegin, zend = self._threadSetupInternal(obj)

        if obj.ThreadType in self.ThreadTypesInternal:
            return (cmd, zbegin, zend)

        # need to reverse direction for external threads
        if cmd == 'G2':
            return ('G3', zbegin, zend)
        return ('G2', zbegin, zend)

    def threadPassRadii(self, obj):
        PathLog.track(obj.Label)
        rMajor = (obj.MajorDiameter.Value - self.tool.Diameter) / 2.0
        rMinor = (obj.MinorDiameter.Value - self.tool.Diameter) / 2.0
        if obj.Passes < 1:
            obj.Passes = 1
        rPass = (rMajor - rMinor) / obj.Passes
        passes = [rMajor]
        for i in range(1, obj.Passes):
            passes.append(rMajor - rPass * i)
        return list(reversed(passes))

    def executeThreadMill(self, obj, loc, gcode, zStart, zFinal, pitch):
        PathLog.track(obj.Label, loc, gcode, zStart, zFinal, pitch)
        elevator = elevatorRadius(obj, loc, self._isThreadInternal(obj), self.tool)

        self.commandlist.append(
            Path.Command("G0", {"Z": obj.ClearanceHeight.Value, "F": self.vertRapid})
        )

        for radius in threadPasses(
            obj.Passes,
            threadRadii,
            self._isThreadInternal(obj),
            obj.MajorDiameter.Value,
            obj.MinorDiameter.Value,
            float(self.tool.Diameter),
            float(self.tool.Crest),
        ):
            commands = threadCommands(loc, gcode, zStart, zFinal, pitch, radius, obj.LeadInOut, elevator)

            for cmd in commands:
                p = cmd.Parameters
                if cmd.Name in ["G0"]:
                    p.update({"F": self.vertRapid})
                if cmd.Name in ["G1", "G2", "G3"]:
                    p.update({"F": self.horizFeed})
                cmd.Parameters = p
            self.commandlist.extend(commands)

        self.commandlist.append(
            Path.Command("G0", {"Z": obj.ClearanceHeight.Value, "F": self.vertRapid})
        )

    def circularHoleExecute(self, obj, holes):
        PathLog.track()
        if self.isToolSupported(obj, self.tool):
            self.commandlist.append(Path.Command("(Begin Thread Milling)"))

            (cmd, zStart, zFinal) = self.threadSetup(obj)
            pitch = obj.Pitch.Value
            if obj.TPI > 0:
                pitch = 25.4 / obj.TPI
            if pitch <= 0:
                PathLog.error("Cannot create thread with pitch {}".format(pitch))
                return

            # rapid to clearance height
            for loc in holes:
                self.executeThreadMill(
                    obj,
                    FreeCAD.Vector(loc["x"], loc["y"], 0),
                    cmd,
                    zStart,
                    zFinal,
                    pitch,
                )
        else:
            PathLog.error("No suitable Tool found for thread milling operation")

    def opSetDefaultValues(self, obj, job):
        PathLog.track()
        obj.ThreadOrientation = self.RightHand
        obj.ThreadType = self.ThreadTypeMetricInternal6H
        obj.ThreadFit = 50
        obj.Pitch = 1
        obj.TPI = 0
        obj.Passes = 1
        obj.Direction = self.DirectionClimb
        obj.LeadInOut = False

    def isToolSupported(self, obj, tool):
        """Thread milling only supports thread milling cutters."""
        support = hasattr(tool, "Diameter") and hasattr(tool, "Crest")
        PathLog.track(tool.Label, support)
        return support


def SetupProperties():
    setup = []
    setup.append("ThreadOrientation")
    setup.append("ThreadType")
    setup.append("ThreadName")
    setup.append("ThreadFit")
    setup.append("MajorDiameter")
    setup.append("MinorDiameter")
    setup.append("Pitch")
    setup.append("TPI")
    setup.append("Passes")
    setup.append("Direction")
    setup.append("LeadInOut")
    return setup


def Create(name, obj=None, parentJob=None):
    """Create(name) ... Creates and returns a thread milling operation."""
    if obj is None:
        obj = FreeCAD.ActiveDocument.addObject("Path::FeaturePython", name)
    obj.Proxy = ObjectThreadMilling(obj, name, parentJob)
    if obj.Proxy:
        obj.Proxy.findAllHoles(obj)
    return obj
