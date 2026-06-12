terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = "~> 5.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# -------------------------------
# Networking: VPC + Subnetwork
# -------------------------------

resource "google_compute_network" "gke_vpc" {
  name                    = var.network
  auto_create_subnetworks = false
  description             = "VPC network for GKE MedQA cluster"
}

resource "google_compute_subnetwork" "gke_subnet" {
  name          = var.subnetwork
  ip_cidr_range = var.subnet_ip_cidr
  region        = var.region
  network       = google_compute_network.gke_vpc.id

  # Secondary ranges for VPC-native (IP alias) GKE
  secondary_ip_range {
    range_name    = var.pods_secondary_range_name
    ip_cidr_range = var.pods_ip_cidr
  }

  secondary_ip_range {
    range_name    = var.services_secondary_range_name
    ip_cidr_range = var.services_ip_cidr
  }

  description = "Subnetwork for GKE MedQA cluster"
}

# -------------------------------
# GKE Standard Cluster
# -------------------------------

resource "google_container_cluster" "gke" {
  name     = var.cluster_name            
  location = var.region                  # regional cluster

  remove_default_node_pool = true
  initial_node_count       = 1

  network    = google_compute_network.gke_vpc.self_link
  subnetwork = google_compute_subnetwork.gke_subnet.self_link

  networking_mode = "VPC_NATIVE"

  ip_allocation_policy {
    cluster_secondary_range_name  = var.pods_secondary_range_name
    services_secondary_range_name = var.services_secondary_range_name
  }

  # Optional but nice to keep up to date
  release_channel {
    channel = var.release_channel
  }

  node_locations = var.node_locations

  description = "Standard GKE cluster for LLM-MedQA-Assistant (CPU-only RAG + external inference)"

  # Basic security best practices
  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  lifecycle {
    ignore_changes = [
      node_config,  # all node config is in separate node_pool
    ]
  }
}

# -------------------------------
# Primary Node Pool
# -------------------------------

resource "google_container_node_pool" "primary" {
  name       = "primary-pool"
  location   = var.region
  cluster    = google_container_cluster.gke.name

  initial_node_count = var.node_min_count

  autoscaling {
    min_node_count = var.node_min_count
    max_node_count = var.node_max_count
  }

  node_config {
    machine_type = var.machine_type

    disk_size_gb = var.node_disk_size_gb
    disk_type    = var.node_disk_type

    oauth_scopes = [
      "https://www.googleapis.com/auth/cloud-platform",
    ]

    labels = {
      workload = "general"
      env      = "medqa"
    }

    tags = ["gke-medqa"]
  }

  management {
    auto_repair  = true
    auto_upgrade = true
  }
}
