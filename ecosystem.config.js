module.exports = {
  apps: [
    {
      name: 'cosmo',
      script: 'tools/cosmo_demo.py',
      interpreter: 'python3',
      interpreter_args: '-u',
      cwd: '/home/pi/robot',
      watch: false,
      autorestart: true,
      restart_delay: 5000,
      exp_backoff_restart_delay: 1000,
      max_restarts: 5,
      max_memory_restart: '1700M',  // picamera2+libcamera baseline ~950MB; was 1200M (too tight)
      kill_timeout: 5000,
      env: {
        PYTHONPATH: '/home/pi/robot',
        // Allow full 4-core usage for vision/numpy/OpenCV heavy ops
        OMP_NUM_THREADS: '4',
        OPENBLAS_NUM_THREADS: '4',
        MKL_NUM_THREADS: '4',
        NUMEXPR_NUM_THREADS: '4',
        GOMP_SPINCOUNT: '0',
        GPIOZERO_PIN_FACTORY: 'lgpio',
        // Behavior tree
        BT_TICK_MS: '100',
        // Gesture: 'auto' = try mediapipe first, fall back to opencv_skin
        GESTURE_BACKEND: 'auto',
        SOUND_DEVICE: 'default',
        // Suppress libcamera INFO/WARN noise — only ERRORs reach stderr
        LIBCAMERA_LOG_LEVELS: '*:ERROR',
      },
      log_date_format: 'YYYY-MM-DD HH:mm:ss',
      // stderr: ALSA/Jack/BlueALSA noise is loud — grep for "robot\." prefix to find real errors
      error_file: '/home/pi/.robot/logs/cosmo-err.log',
      out_file: '/home/pi/.robot/logs/cosmo-out.log',
      merge_logs: false,
    },
  ]
};
