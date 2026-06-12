pipeline {
  agent {
    kubernetes {
      defaultContainer 'python'
      yaml """
apiVersion: v1
kind: Pod
spec:
  serviceAccountName: jenkins

  volumes:
    - name: docker-sock
      hostPath:
        path: /var/run/docker.sock

  containers:
    - name: python
      image: python:3.11-slim
      command:
        - cat
      tty: true

    - name: checkov
      image: bridgecrew/checkov:latest
      command:
        - cat
      tty: true

    - name: docker
      image: docker:24-cli
      command:
        - cat
      tty: true
      volumeMounts:
        - name: docker-sock
          mountPath: /var/run/docker.sock

    - name: kubectl
      image: bitnami/kubectl:1.30
      command:
        - cat
      tty: true

    - name: sonar
      image: sonarsource/sonar-scanner-cli:latest
      command:
        - cat
      tty: true
"""
    }
  }

  environment {
    // ---------------- Local Minikube ----------------
    HELM_NAMESPACE = 'model-serving'

    // ---------------- Local Image Tags ----------------
    RAG_VERSION    = "${env.BUILD_NUMBER}"
    UI_VERSION     = "${env.BUILD_NUMBER}"
    INGEST_VERSION = "${env.BUILD_NUMBER}"

    RAG_IMAGE      = "rag-orchestrator:${env.BUILD_NUMBER}"
    UI_IMAGE       = "streamlit-ui:${env.BUILD_NUMBER}"
    INGEST_IMAGE   = "qdrant-ingestor:${env.BUILD_NUMBER}"

    PYTHONUNBUFFERED = '1'
  }

  options {
    timestamps()
    disableConcurrentBuilds()
  }

  stages {

    stage('Checkout') {
      steps {
        checkout scm
      }
    }

    stage('Preflight - Check Local CI/CD Runtime') {
      steps {
        container('docker') {
          sh '''
            set -e

            echo "=== Docker version ==="
            docker version

            echo "=== Docker info ==="
            docker info

            echo "=== Current Docker images ==="
            docker images | head || true
          '''
        }

        container('kubectl') {
          sh '''
            set -e

            echo "=== Kubernetes access ==="
            kubectl get nodes

            echo "=== Namespaces ==="
            kubectl get ns

            echo "=== Current model-serving pods ==="
            kubectl get pods -n ${HELM_NAMESPACE}
          '''
        }
      }
    }

    stage('Unit Tests - rag-orchestrator') {
      steps {
        container('python') {
          dir('services/rag-orchestrator') {
            sh '''
              set -e

              echo "=== Python version ==="
              python --version

              echo "=== Install test dependencies ==="
              python -m pip install --upgrade pip setuptools wheel
              python -m pip install pytest pytest-cov

              if [ -f requirements.txt ]; then
                echo "=== Install application requirements ==="
                python -m pip install -r requirements.txt || true
              fi

              echo "=== Run unit tests ==="
              if [ -d tests ]; then
                python -m pytest tests -q --disable-warnings
              elif [ -d ../../tests ]; then
                python -m pytest ../../tests -q --disable-warnings
              else
                echo "No tests directory found. Skip pytest for local demo."
              fi
            '''
          }
        }
      }
    }

    stage('IaC Security Scan - Checkov') {
      steps {
        container('checkov') {
          sh '''
            set -e

            echo "=== Checkov scan for Helm/Kubernetes files ==="

            if [ -d charts ]; then
              checkov -d charts --framework helm,kubernetes --quiet --soft-fail
            else
              echo "No charts directory found. Skip Checkov."
            fi

            if [ -d k8s ]; then
              checkov -d k8s --framework kubernetes --quiet --soft-fail
            else
              echo "No k8s directory found. Skip k8s Checkov scan."
            fi
          '''
        }
      }
    }

    stage('SonarQube Scan - Optional') {
      steps {
        container('sonar') {
          sh '''
            set -e

            echo "=== SonarQube scan ==="

            if [ -z "$SONAR_HOST_URL" ] || [ -z "$SONAR_TOKEN" ]; then
              echo "SONAR_HOST_URL or SONAR_TOKEN is not configured."
              echo "Skip SonarQube scan for local demo."
              exit 0
            fi

            sonar-scanner \
              -Dsonar.projectKey=LLM-MedQA-Assistant \
              -Dsonar.projectName=LLM-MedQA-Assistant \
              -Dsonar.sources=. \
              -Dsonar.host.url=$SONAR_HOST_URL \
              -Dsonar.token=$SONAR_TOKEN
          '''
        }
      }
    }

    stage('Build Docker Images in Minikube') {
      steps {
        container('docker') {
          sh '''
            set -e

            echo "=== Docker daemon info ==="
            docker version

            echo "=== Build rag-orchestrator image ==="
            docker build \
              -f services/rag-orchestrator/Dockerfile \
              -t ${RAG_IMAGE} \
              .

            echo "=== Build streamlit-ui image ==="
            docker build \
              -f services/streamlit-ui/Dockerfile \
              -t ${UI_IMAGE} \
              .

            echo "=== Build qdrant-ingestor image ==="
            docker build \
              -f services/qdrant-ingestor/Dockerfile \
              -t ${INGEST_IMAGE} \
              .

            echo "=== Built images ==="
            docker images | grep -E "rag-orchestrator|streamlit-ui|qdrant-ingestor" || true
          '''
        }
      }
    }

    stage('Deploy to Local Minikube') {
      steps {
        container('kubectl') {
          sh '''
            set -e

            echo "=== Current pods before deploy ==="
            kubectl get pods -n ${HELM_NAMESPACE}

            echo "=== Update rag-orchestrator deployment image ==="
            kubectl -n ${HELM_NAMESPACE} set image deployment/rag-orchestrator \
              rag-orchestrator=${RAG_IMAGE}

            kubectl -n ${HELM_NAMESPACE} patch deployment rag-orchestrator \
              -p '{"spec":{"template":{"spec":{"containers":[{"name":"rag-orchestrator","imagePullPolicy":"Never"}]}}}}'

            echo "=== Update streamlit deployment image ==="
            kubectl -n ${HELM_NAMESPACE} set image deployment/streamlit \
              streamlit=${UI_IMAGE}

            kubectl -n ${HELM_NAMESPACE} patch deployment streamlit \
              -p '{"spec":{"template":{"spec":{"containers":[{"name":"streamlit","imagePullPolicy":"Never"}]}}}}'

            echo "=== Update qdrant-ingestor deployment image if deployment exists ==="
            if kubectl -n ${HELM_NAMESPACE} get deployment qdrant-ingestor >/dev/null 2>&1; then
              kubectl -n ${HELM_NAMESPACE} set image deployment/qdrant-ingestor \
                qdrant-ingestor=${INGEST_IMAGE}

              kubectl -n ${HELM_NAMESPACE} patch deployment qdrant-ingestor \
                -p '{"spec":{"template":{"spec":{"containers":[{"name":"qdrant-ingestor","imagePullPolicy":"Never"}]}}}}'
            else
              echo "qdrant-ingestor deployment not found. Skip deployment update."
            fi

            echo "=== Wait for rollout ==="
            kubectl rollout status deployment/rag-orchestrator -n ${HELM_NAMESPACE} --timeout=180s
            kubectl rollout status deployment/streamlit -n ${HELM_NAMESPACE} --timeout=180s

            if kubectl -n ${HELM_NAMESPACE} get deployment qdrant-ingestor >/dev/null 2>&1; then
              kubectl rollout status deployment/qdrant-ingestor -n ${HELM_NAMESPACE} --timeout=180s
            fi

            echo "=== Current pods after deploy ==="
            kubectl get pods -n ${HELM_NAMESPACE}
          '''
        }
      }
    }

    stage('Smoke Test - Local Deployment') {
      steps {
        container('kubectl') {
          sh '''
            set -e

            echo "=== Services ==="
            kubectl get svc -n ${HELM_NAMESPACE}

            echo "=== Deployments ==="
            kubectl get deploy -n ${HELM_NAMESPACE}

            echo "=== Pods wide ==="
            kubectl get pods -n ${HELM_NAMESPACE} -o wide
          '''
        }
      }
    }
  }

  post {
    success {
      echo 'Local CI/CD pipeline succeeded — model-serving deployed to Minikube'
    }

    failure {
      echo 'Local CI/CD pipeline failed — check logs above'
    }

    always {
      echo 'Local CI/CD pipeline finished'
    }
  }
}