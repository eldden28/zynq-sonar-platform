(() => {
  "use strict";

  const TAU = Math.PI * 2;
  const DEG = Math.PI / 180;

  const clamp = (value, minimum, maximum) =>
    Math.max(minimum, Math.min(maximum, value));
  const wrapRadians = (value) => {
    while (value > Math.PI) value -= TAU;
    while (value < -Math.PI) value += TAU;
    return value;
  };
  const add = (a, b) => [a[0] + b[0], a[1] + b[1], a[2] + b[2]];
  const subtract = (a, b) => [a[0] - b[0], a[1] - b[1], a[2] - b[2]];
  const scale = (value, amount) => [
    value[0] * amount,
    value[1] * amount,
    value[2] * amount,
  ];
  const dot = (a, b) => a[0] * b[0] + a[1] * b[1] + a[2] * b[2];
  const cross = (a, b) => [
    a[1] * b[2] - a[2] * b[1],
    a[2] * b[0] - a[0] * b[2],
    a[0] * b[1] - a[1] * b[0],
  ];
  const normalize = (value) => {
    const length = Math.hypot(value[0], value[1], value[2]) || 1;
    return scale(value, 1 / length);
  };

  function bodyToScene(point, roll, pitch, heading) {
    const cr = Math.cos(roll);
    const sr = Math.sin(roll);
    const cp = Math.cos(pitch);
    const sp = Math.sin(pitch);
    const cy = Math.cos(heading);
    const sy = Math.sin(heading);
    const x = point[0];
    const y = point[1];
    const z = point[2];

    // Aerospace body-to-NED rotation: Rz(heading) Ry(pitch) Rx(roll).
    const north =
      cy * cp * x +
      (cy * sp * sr - sy * cr) * y +
      (cy * sp * cr + sy * sr) * z;
    const east =
      sy * cp * x +
      (sy * sp * sr + cy * cr) * y +
      (sy * sp * cr - cy * sr) * z;
    const down = -sp * x + cp * sr * y + cp * cr * z;

    // Scene coordinates are east, up, north.
    return [east, -down, north];
  }

  function colorChannels(hex) {
    const value = Number.parseInt(hex.slice(1), 16);
    return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
  }

  function litColor(hex, amount) {
    const [red, green, blue] = colorChannels(hex);
    const factor = clamp(amount, 0.24, 1.35);
    return `rgb(${Math.round(clamp(red * factor, 0, 255))},` +
      `${Math.round(clamp(green * factor, 0, 255))},` +
      `${Math.round(clamp(blue * factor, 0, 255))})`;
  }

  function quad(a, b, c, d, color, edge = true) {
    return { points: [a, b, c, d], color, edge };
  }

  function triangle(a, b, c, color, edge = true) {
    return { points: [a, b, c], color, edge };
  }

  function prismFaces(front, back, color) {
    const faces = [quad(...front, color), quad(...back.slice().reverse(), color)];
    for (let index = 0; index < front.length; index += 1) {
      const next = (index + 1) % front.length;
      faces.push(quad(front[index], front[next], back[next], back[index], color));
    }
    return faces;
  }

  function buildVehicle() {
    const faces = [];
    const segments = 28;
    const profile = [
      [-2.38, 0.10],
      [-2.20, 0.26],
      [-1.95, 0.42],
      [-1.72, 0.49],
      [-0.86, 0.51],
      [-0.72, 0.51],
      [0.66, 0.51],
      [0.82, 0.51],
      [1.77, 0.49],
      [2.14, 0.42],
      [2.43, 0.25],
      [2.57, 0.04],
    ];
    const hullColor = (x) => {
      if (x < -1.72) return "#2b3139";
      if ((x > -0.87 && x < -0.70) || (x > 0.64 && x < 0.84)) {
        return "#1d2229";
      }
      if (x > 2.13) return "#e5bd20";
      return "#d9ad12";
    };

    for (let ring = 0; ring < profile.length - 1; ring += 1) {
      const [x0, radius0] = profile[ring];
      const [x1, radius1] = profile[ring + 1];
      const color = hullColor((x0 + x1) / 2);
      for (let segment = 0; segment < segments; segment += 1) {
        const angle0 = (segment / segments) * TAU;
        const angle1 = ((segment + 1) / segments) * TAU;
        faces.push(
          quad(
            [x0, Math.cos(angle0) * radius0, Math.sin(angle0) * radius0],
            [x1, Math.cos(angle0) * radius1, Math.sin(angle0) * radius1],
            [x1, Math.cos(angle1) * radius1, Math.sin(angle1) * radius1],
            [x0, Math.cos(angle1) * radius0, Math.sin(angle1) * radius0],
            color,
            false,
          ),
        );
      }
    }

    // Four independent stern control planes.
    const finColor = "#d4a70f";
    const verticalShape = [
      [-2.24, 0, -0.35],
      [-1.70, 0, -0.46],
      [-1.32, 0, -1.08],
      [-2.08, 0, -0.86],
    ];
    faces.push(
      ...prismFaces(
        verticalShape.map(([x, , z]) => [x, -0.035, z]),
        verticalShape.map(([x, , z]) => [x, 0.035, z]),
        finColor,
      ),
    );
    faces.push(
      ...prismFaces(
        verticalShape.map(([x, , z]) => [x, -0.035, -z]),
        verticalShape.map(([x, , z]) => [x, 0.035, -z]),
        finColor,
      ),
    );
    const horizontalShape = [
      [-2.24, -0.35, 0],
      [-1.70, -0.46, 0],
      [-1.32, -1.08, 0],
      [-2.08, -0.86, 0],
    ];
    faces.push(
      ...prismFaces(
        horizontalShape.map(([x, y]) => [x, y, -0.035]),
        horizontalShape.map(([x, y]) => [x, y, 0.035]),
        finColor,
      ),
    );
    faces.push(
      ...prismFaces(
        horizontalShape.map(([x, y]) => [x, -y, -0.035]),
        horizontalShape.map(([x, y]) => [x, -y, 0.035]),
        finColor,
      ),
    );

    // Three-blade bronze propulsor.
    for (let blade = 0; blade < 3; blade += 1) {
      const angle = (blade / 3) * TAU;
      const tangent = [-Math.sin(angle), Math.cos(angle)];
      const radial = [Math.cos(angle), Math.sin(angle)];
      const root = [
        -2.46,
        radial[0] * 0.10 - tangent[0] * 0.05,
        radial[1] * 0.10 - tangent[1] * 0.05,
      ];
      const shoulder = [
        -2.51,
        radial[0] * 0.48 - tangent[0] * 0.16,
        radial[1] * 0.48 - tangent[1] * 0.16,
      ];
      const tip = [
        -2.55,
        radial[0] * 0.69 + tangent[0] * 0.04,
        radial[1] * 0.69 + tangent[1] * 0.04,
      ];
      const trailing = [
        -2.49,
        radial[0] * 0.23 + tangent[0] * 0.12,
        radial[1] * 0.23 + tangent[1] * 0.12,
      ];
      faces.push(quad(root, shoulder, tip, trailing, "#b67831"));
    }

    // Compact navigation mast and forward sensor window.
    faces.push(
      ...prismFaces(
        [
          [0.94, -0.08, -0.48],
          [1.14, -0.08, -0.48],
          [1.12, -0.08, -0.85],
          [0.96, -0.08, -0.85],
        ],
        [
          [0.94, 0.08, -0.48],
          [1.14, 0.08, -0.48],
          [1.12, 0.08, -0.85],
          [0.96, 0.08, -0.85],
        ],
        "#262d36",
      ),
    );
    faces.push(
      ...prismFaces(
        [
          [1.00, -0.095, -0.84],
          [1.10, -0.095, -0.84],
          [1.10, -0.095, -0.97],
          [1.00, -0.095, -0.97],
        ],
        [
          [1.00, 0.095, -0.84],
          [1.10, 0.095, -0.84],
          [1.10, 0.095, -0.97],
          [1.00, 0.095, -0.97],
        ],
        "#ff8a3d",
      ),
    );
    faces.push(
      quad(
        [2.35, -0.23, -0.16],
        [2.48, -0.11, -0.08],
        [2.48, 0.11, -0.08],
        [2.35, 0.23, -0.16],
        "#17283a",
      ),
    );

    // Small hub rendered last by depth sorting with the other surfaces.
    const hubProfile = [
      [-2.58, 0.02],
      [-2.50, 0.14],
      [-2.35, 0.15],
      [-2.29, 0.09],
    ];
    for (let ring = 0; ring < hubProfile.length - 1; ring += 1) {
      for (let segment = 0; segment < 18; segment += 1) {
        const a0 = (segment / 18) * TAU;
        const a1 = ((segment + 1) / 18) * TAU;
        const [x0, r0] = hubProfile[ring];
        const [x1, r1] = hubProfile[ring + 1];
        faces.push(
          quad(
            [x0, Math.cos(a0) * r0, Math.sin(a0) * r0],
            [x1, Math.cos(a0) * r1, Math.sin(a0) * r1],
            [x1, Math.cos(a1) * r1, Math.sin(a1) * r1],
            [x0, Math.cos(a1) * r0, Math.sin(a1) * r0],
            "#7e8791",
            false,
          ),
        );
      }
    }
    return faces;
  }

  class CoraAttitudeViewer {
    constructor(canvas) {
      this.canvas = canvas;
      this.context = canvas.getContext("2d");
      this.faces = buildVehicle();
      this.current = { roll: 0, pitch: 0, heading: 0 };
      this.target = { roll: 0, pitch: 0, heading: 0 };
      this.camera = { azimuth: -0.78, elevation: 0.33, distance: 9.4 };
      this.drag = null;
      this.pointers = new Map();
      this.pinch = null;
      this.lastFrame = performance.now();
      this.bindPointerControls();
      this.resizeObserver = new ResizeObserver(() => this.resize());
      this.resizeObserver.observe(canvas);
      this.resize();
      requestAnimationFrame((time) => this.animate(time));
    }

    update(solution = {}) {
      if (!Number.isFinite(solution.roll_deg)) return;
      this.target.roll = solution.roll_deg * DEG;
      this.target.pitch = solution.pitch_deg * DEG;
      const desired = (solution.heading_deg || 0) * DEG;
      this.target.heading += wrapRadians(desired - this.target.heading);
      const setText = (id, value) => {
        const element = document.getElementById(id);
        if (element) element.textContent = value;
      };
      setText("modelHeading", `${solution.heading_deg.toFixed(1)}°`);
      setText("modelRoll", `${solution.roll_deg.toFixed(1)}°`);
      setText("modelPitch", `${solution.pitch_deg.toFixed(1)}°`);
    }

    bindPointerControls() {
      this.canvas.addEventListener("pointerdown", (event) => {
        this.canvas.setPointerCapture(event.pointerId);
        this.pointers.set(event.pointerId, [event.clientX, event.clientY]);
        if (this.pointers.size === 1) {
          this.drag = {
            pointerId: event.pointerId,
            x: event.clientX,
            y: event.clientY,
            azimuth: this.camera.azimuth,
            elevation: this.camera.elevation,
          };
        } else if (this.pointers.size === 2) {
          const [a, b] = [...this.pointers.values()];
          this.pinch = {
            span: Math.hypot(b[0] - a[0], b[1] - a[1]),
            x: (a[0] + b[0]) / 2,
            y: (a[1] + b[1]) / 2,
            distance: this.camera.distance,
            azimuth: this.camera.azimuth,
            elevation: this.camera.elevation,
          };
          this.drag = null;
        }
      });
      this.canvas.addEventListener("pointermove", (event) => {
        if (!this.pointers.has(event.pointerId)) return;
        this.pointers.set(event.pointerId, [event.clientX, event.clientY]);
        if (this.pointers.size >= 2 && this.pinch) {
          const [a, b] = [...this.pointers.values()];
          const span = Math.max(Math.hypot(b[0] - a[0], b[1] - a[1]), 1);
          const x = (a[0] + b[0]) / 2;
          const y = (a[1] + b[1]) / 2;
          this.camera.distance = clamp(
            (this.pinch.distance * this.pinch.span) / span,
            7.0,
            13.0,
          );
          this.camera.azimuth =
            this.pinch.azimuth + (x - this.pinch.x) * 0.008;
          this.camera.elevation = clamp(
            this.pinch.elevation - (y - this.pinch.y) * 0.008,
            -1.05,
            1.05,
          );
          return;
        }
        if (!this.drag || this.drag.pointerId !== event.pointerId) return;
        this.camera.azimuth =
          this.drag.azimuth + (event.clientX - this.drag.x) * 0.008;
        this.camera.elevation = clamp(
          this.drag.elevation - (event.clientY - this.drag.y) * 0.008,
          -1.05,
          1.05,
        );
      });
      const stopDrag = (event) => {
        this.pointers.delete(event.pointerId);
        this.pinch = null;
        if (this.pointers.size === 1) {
          const [pointerId, [x, y]] = [...this.pointers.entries()][0];
          this.drag = {
            pointerId,
            x,
            y,
            azimuth: this.camera.azimuth,
            elevation: this.camera.elevation,
          };
        } else {
          this.drag = null;
        }
      };
      this.canvas.addEventListener("pointerup", stopDrag);
      this.canvas.addEventListener("pointercancel", stopDrag);
      this.canvas.addEventListener("dblclick", () => {
        this.camera.azimuth = -0.78;
        this.camera.elevation = 0.33;
      });
      this.canvas.addEventListener(
        "wheel",
        (event) => {
          event.preventDefault();
          this.camera.distance = clamp(
            this.camera.distance + Math.sign(event.deltaY) * 0.55,
            7.0,
            13.0,
          );
        },
        { passive: false },
      );
    }

    resize() {
      const bounds = this.canvas.getBoundingClientRect();
      const ratio = Math.min(window.devicePixelRatio || 1, 2);
      const width = Math.max(Math.round(bounds.width * ratio), 1);
      const height = Math.max(Math.round(bounds.height * ratio), 1);
      if (this.canvas.width !== width || this.canvas.height !== height) {
        this.canvas.width = width;
        this.canvas.height = height;
      }
      this.ratio = ratio;
      this.width = bounds.width;
      this.height = bounds.height;
    }

    cameraFrame() {
      const position = [
        Math.sin(this.camera.azimuth) *
          Math.cos(this.camera.elevation) *
          this.camera.distance,
        Math.sin(this.camera.elevation) * this.camera.distance,
        Math.cos(this.camera.azimuth) *
          Math.cos(this.camera.elevation) *
          this.camera.distance,
      ];
      const forward = normalize(scale(position, -1));
      const right = normalize(cross(forward, [0, 1, 0]));
      const up = normalize(cross(right, forward));
      return { position, forward, right, up };
    }

    project(point, camera) {
      const relative = subtract(point, camera.position);
      const depth = dot(relative, camera.forward);
      const focal = Math.min(this.width, this.height) * 1.18;
      return {
        x: this.width / 2 + (dot(relative, camera.right) * focal) / depth,
        y:
          this.height * 0.47 -
          (dot(relative, camera.up) * focal) / depth,
        depth,
      };
    }

    drawBackdrop(context, camera) {
      const gradient = context.createRadialGradient(
        this.width * 0.48,
        this.height * 0.38,
        10,
        this.width * 0.48,
        this.height * 0.38,
        this.width * 0.75,
      );
      gradient.addColorStop(0, "#10263a");
      gradient.addColorStop(0.55, "#071522");
      gradient.addColorStop(1, "#03070d");
      context.fillStyle = gradient;
      context.fillRect(0, 0, this.width, this.height);

      context.strokeStyle = "#4ee4d21c";
      context.lineWidth = 1;
      const gridY = -1.25;
      for (let value = -8; value <= 8; value += 1) {
        this.drawWorldLine(
          context,
          [-8, gridY, value],
          [8, gridY, value],
          camera,
        );
        this.drawWorldLine(
          context,
          [value, gridY, -8],
          [value, gridY, 8],
          camera,
        );
      }

      const glow = context.createLinearGradient(0, 0, 0, this.height);
      glow.addColorStop(0, "#7ad9ff08");
      glow.addColorStop(0.62, "#4ee4d208");
      glow.addColorStop(1, "#00000000");
      context.fillStyle = glow;
      context.fillRect(0, 0, this.width, this.height);
    }

    drawWorldLine(context, start, end, camera) {
      const a = this.project(start, camera);
      const b = this.project(end, camera);
      if (a.depth <= 0 || b.depth <= 0) return;
      context.beginPath();
      context.moveTo(a.x, a.y);
      context.lineTo(b.x, b.y);
      context.stroke();
    }

    drawVehicle(context, camera) {
      const roll = this.current.roll;
      const pitch = this.current.pitch;
      const heading = this.current.heading;
      const light = normalize([-0.35, 0.82, -0.45]);
      const renderFaces = [];

      for (const face of this.faces) {
        const world = face.points.map((point) =>
          bodyToScene(point, roll, pitch, heading),
        );
        const projected = world.map((point) => this.project(point, camera));
        if (projected.some((point) => point.depth <= 0.1)) continue;
        const normal = normalize(
          cross(subtract(world[1], world[0]), subtract(world[2], world[0])),
        );
        const center = scale(
          world.reduce((total, point) => add(total, point), [0, 0, 0]),
          1 / world.length,
        );
        const view = normalize(subtract(camera.position, center));
        const facing = Math.abs(dot(normal, view));
        const brightness =
          0.38 + Math.max(dot(normal, light), dot(scale(normal, -1), light)) * 0.64;
        renderFaces.push({
          projected,
          depth:
            projected.reduce((sum, point) => sum + point.depth, 0) /
            projected.length,
          fill: litColor(face.color, brightness),
          edge: face.edge,
          facing,
        });
      }

      renderFaces.sort((a, b) => b.depth - a.depth);
      context.lineJoin = "round";
      for (const face of renderFaces) {
        context.beginPath();
        face.projected.forEach((point, index) => {
          if (index) context.lineTo(point.x, point.y);
          else context.moveTo(point.x, point.y);
        });
        context.closePath();
        context.fillStyle = face.fill;
        context.fill();
        if (face.edge && face.facing > 0.06) {
          context.strokeStyle = "#02060b70";
          context.lineWidth = 0.75;
          context.stroke();
        }
      }

      // Vehicle-forward vector.
      const nose = this.project(
        bodyToScene([3.25, 0, 0], roll, pitch, heading),
        camera,
      );
      const center = this.project([0, 0, 0], camera);
      context.strokeStyle = "#4ee4d2a8";
      context.fillStyle = "#4ee4d2";
      context.lineWidth = 2;
      context.beginPath();
      context.moveTo(center.x, center.y);
      context.lineTo(nose.x, nose.y);
      context.stroke();
      const angle = Math.atan2(nose.y - center.y, nose.x - center.x);
      context.beginPath();
      context.moveTo(nose.x, nose.y);
      context.lineTo(
        nose.x - Math.cos(angle - 0.45) * 9,
        nose.y - Math.sin(angle - 0.45) * 9,
      );
      context.lineTo(
        nose.x - Math.cos(angle + 0.45) * 9,
        nose.y - Math.sin(angle + 0.45) * 9,
      );
      context.closePath();
      context.fill();
    }

    drawCompass(context) {
      const x = 42;
      const y = 42;
      const radius = 25;
      context.strokeStyle = "#7890ad66";
      context.lineWidth = 1;
      context.beginPath();
      context.arc(x, y, radius, 0, TAU);
      context.stroke();
      context.save();
      context.translate(x, y);
      context.rotate(-this.current.heading);
      context.strokeStyle = "#ff697d";
      context.fillStyle = "#ff697d";
      context.lineWidth = 2;
      context.beginPath();
      context.moveTo(0, 17);
      context.lineTo(0, -18);
      context.stroke();
      context.beginPath();
      context.moveTo(0, -23);
      context.lineTo(-5, -14);
      context.lineTo(5, -14);
      context.closePath();
      context.fill();
      context.restore();
      context.fillStyle = "#dce9fb";
      context.font = "700 10px ui-monospace, monospace";
      context.textAlign = "center";
      context.fillText("N", x, 12);
    }

    render() {
      const context = this.context;
      context.setTransform(this.ratio, 0, 0, this.ratio, 0, 0);
      const camera = this.cameraFrame();
      this.drawBackdrop(context, camera);
      this.drawVehicle(context, camera);
      this.drawCompass(context);
    }

    animate(time) {
      const dt = clamp((time - this.lastFrame) / 1000, 0, 0.1);
      this.lastFrame = time;
      const response = 1 - Math.exp(-dt * 6.5);
      this.current.roll +=
        wrapRadians(this.target.roll - this.current.roll) * response;
      this.current.pitch +=
        wrapRadians(this.target.pitch - this.current.pitch) * response;
      this.current.heading +=
        wrapRadians(this.target.heading - this.current.heading) * response;
      this.render();
      requestAnimationFrame((nextTime) => this.animate(nextTime));
    }
  }

  window.CoraAttitudeViewer = CoraAttitudeViewer;
})();
